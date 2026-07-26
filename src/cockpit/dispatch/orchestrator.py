"""orchestrator — boucle de dispatch parallèle sur la roadmap d'un projet (`cockpit run`). Enchaîne le
DAG intra-feature ET parallélise les features **indépendantes déjà prêtes** : une feature = branche =
worktree = **mutex** (1 worker à la fois) → N features prêtes ⇒ N workers concurrents.

Deux invariants portés du plan (cockpit-typed-bundles) :
- **La boucle possède la policy d'avancement du DAG.** `dispatch_next` (mode mono) laisse la task
  `in_progress` sur succès (le `done` est déféré au merge). Le résolveur mappe `in_progress→ACTIVE` ⇒ sans
  transition `done`, le DAG n'avance jamais. **Ici**, après un run réussi, la boucle marque la task `done`
  (dans la connexion du thread) — SANS toucher `dispatch_next` (préserve la sémantique mono + ses tests).
  Sémantique : task `done` = worker OK + commit posé ; merge de la **feature** = gate humain séparé.
- **Concurrence SQLite** : connexion **par thread** (`_dispatch_one` ouvre la sienne — `check_same_thread`),
  `PRAGMA busy_timeout` (posé dans `store.connect`) absorbe les rares chevauchements d'écriture. Le thread
  principal est **seul assignateur** : `in_flight` (mutex par feature) est muté **à la soumission**, jamais
  dans un thread worker → zéro double-dispatch.

Deps **inter-features** (v10) : une feature reste non-dispatchable tant qu'une prérequise n'est pas `merged`
(pré-filtre `_discoverable_features` via `resolver.classify_features` — le cas design→code est enforce, plus
« résolu à la main par l'ordre de merge »). Hors V1 : merge auto (la boucle ne produit que des commits sur
branches feature ; `cockpit merge --go` reste un gate humain séparé).
"""
from __future__ import annotations

import argparse
import contextlib
import signal
import sqlite3
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from cockpit import auth, interview
from cockpit.config import Settings
from cockpit.db import store
from cockpit.dispatch import abort, reviewer, worker
from cockpit.dispatch import worktree as worktree_mod
from cockpit.gate import merge as merge_gate
from cockpit.gate import toolchain, verify
from cockpit.git.backend import GitBackend
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects.registry import sot_path_for
from cockpit.roadmap import model, resolver
from cockpit.tools import tools_env

_BASE_BRANCH = "dev"   # base du diff finalisé (Tier-0 + review), main-suit-dev

DEFAULT_MAX_PARALLEL = 2   # 2 workers concurrents par défaut (borne prudente ; --max-parallel l'ajuste)

# Features hors-jeu pour le dispatch : déjà mergées ou annulées (plus rien à drainer).
_INERT_FEATURE_STATUS = frozenset({"merged", "cancelled"})

# États du DAG INTER-feature (v10) qui interdisent le dispatch : prérequis non-mergé (BLOCKED_DEPS) ou graphe
# malformé (ERROR/CYCLE, déjà flagué par `check`, défensif ici). READY/ACTIVE passent (feature drainable).
_BLOCKED_FEATURE_STATES = frozenset({"BLOCKED_DEPS", "ERROR", "CYCLE"})


def _discoverable_features(conn: sqlite3.Connection, project: str,
                           in_flight: set[str], failed: set[str]) -> list[str]:
    """Slugs des features **dispatchables maintenant** : ni inertes (merged/cancelled), ni en vol, ni en
    échec, dont le DAG **inter-feature** est débloqué (toutes les prérequises `merged`), et dont le résolveur
    DAG intra-feature expose une NEXT task READY. Triées par le rang de cette NEXT (priorité ↑, création ↑,
    slug) → les priorités hautes soumises d'abord. Read-only (connexion principale)."""
    feat_states = resolver.classify_features(conn, project)   # DAG inter-feature (v10), 1× par passe
    ranked: list[tuple[tuple, str]] = []
    for f in model.list_features(conn, project):
        slug = f["slug"]
        if f["status"] in _INERT_FEATURE_STATUS or slug in in_flight or slug in failed:
            continue
        if feat_states[slug]["state"] in _BLOCKED_FEATURE_STATES:
            continue                                          # prérequis inter-feature non mergé → pas encore
        index = resolver.index_for_feature(conn, f"{project}/{slug}")
        if not index:
            continue
        nxt = resolver.resolve_next(index)
        if nxt is None:
            continue
        key = (resolver.PRIO.get(nxt["priority"], resolver._UNKNOWN_PRIO),
               nxt.get("created_at") or "9999", slug)
        ranked.append((key, slug))
    return [slug for _, slug in sorted(ranked)]


def _dispatch_one(settings: Settings, project: str, feature: str,
                  git: GitBackend, runner: worker.Runner | None) -> dict:
    """Dispatche la NEXT task d'UNE feature, dans une **connexion propre au thread**. Sur succès, marque la
    task `done` (la boucle possède cette transition — cf. module docstring). Retourne un rapport
    `{feature, task, ok, reason}` (jamais d'exception vers la boucle : le worker échoue, il ne plante pas)."""
    conn = store.connect(settings.db_path)   # thread-local (check_same_thread) ; base déjà migrée
    try:
        feature_ref = f"{project}/{feature}"
        report = worker.dispatch_next(conn, settings, feature_ref=feature_ref, git=git, runner=runner)
        ok = bool(report.get("dispatched") and report.get("result", {}).get("ok"))
        if ok:
            # dispatch_next a laissé la task `in_progress` (+ committé le worktree) → la boucle la clôt.
            feat = model.resolve_feature(conn, feature_ref)
            conn.execute("UPDATE tasks SET status = 'done' WHERE feature_id = ? AND slug = ?",
                         (feat["id"], report["task"]))
            conn.commit()
        return {"feature": feature, "task": report.get("task"), "ok": ok,
                "reason": report.get("reason", "?"),
                "needs_terminal": bool(report.get("needs_terminal")),   # v12 : task interactive → interview
                "rate_limited": bool(report.get("result", {}).get("rate_limited")),   # v15 : plafond 5h org
                "interrupted": bool(report.get("result", {}).get("interrupted"))}   # v16 : SIGTERM externe
    finally:
        conn.close()


def _worked_complete_features(conn: sqlite3.Connection, project: str, failed: set[str]) -> list[str]:
    """Features dont le TRAVAIL est fini (toutes tasks `done`, ≥1 task), encore `active` (ni merged ni
    cancelled), et non en échec → prêtes à être **finalisées** (Tier-0 + review → merge-ready). Read-only."""
    out: list[str] = []
    for f in model.list_features(conn, project):
        slug = f["slug"]
        if f["status"] in _INERT_FEATURE_STATUS or slug in failed:
            continue
        rows = conn.execute(
            "SELECT status FROM tasks WHERE feature_id = ?", (f["id"],)).fetchall()
        if rows and all(r["status"] in ("done", "cancelled") for r in rows) \
                and any(r["status"] == "done" for r in rows):
            out.append(slug)
    return out


def finalize_feature(conn: sqlite3.Connection, settings: Settings, project: str, feature: str, *,
                     review_runner: reviewer.Runner | None) -> dict:
    """Finalise une feature au travail fini : exécute le **Tier-0 toolchain** (déterministe, SHA-bound) PUIS
    **dispatche le reviewer Tier-1** (charte : LLM génère / déterministe gate), et évalue le gate en *preview*
    (`human_go=False` → on ne merge JAMAIS ici — le GO reste humain). Retourne `{feature, merge_ready,
    blockers, review}`. Best-effort : une pièce en échec laisse le gate bloquer proprement (surfacé)."""
    feature_ref = f"{project}/{feature}"
    git = InternalGit()                                   # lecture git + gate (distinct du transport worker)
    feat = model.resolve_feature(conn, feature_ref)
    sot = sot_path_for(settings, project)
    wt = worktree_mod.worktree_path_for(settings, project, feature)
    try:
        sha = git.feature_sha(sot, feat["branch"])
        diff_files = git.diff_names(sot, base=_BASE_BRANCH, head=feat["branch"])
    except GitOpError:
        return {"feature": feature, "merge_ready": False, "review": None,
                "blockers": [f"branche {feat['branch']} absente — jamais dispatchée"]}
    if wt.is_dir():                                       # Tier-0 : la toolchain native, dans le worktree
        results = toolchain.run_toolchain(wt, diff_files, env=tools_env(settings))
        toolchain.write_verdict(settings, project, feature, results, sha=sha, conn=conn)
    # Un livrable docs-only (prose seule, ex. socle-design) n'a pas de code à reviewer : ne PAS gaspiller un
    # worker de review (le gate traite Tier-1 N/A côté `evaluate_gate` — même prédicat `is_docs_only`).
    if toolchain.is_docs_only(diff_files):
        review_report: dict = {"reviewed": False, "reason": "docs-only — review de code N/A"}
    else:
        review_report = reviewer.dispatch_reviewer(conn, settings, feature_ref=feature_ref, git=git,
                                                   runner=review_runner)
    # Tier-1.5 AUTO : une feature qui touche une surface UI (trigger partagé `has_visual_change`) et n'a pas
    # encore de preuve fraîche → preview-déploie son worktree + prouve les markers déclarés. C'est ce qui
    # ferme une feature VISUELLE en autonomie (sans override). Best-effort, fail-CLOSED : type non
    # hébergeable / podman absent → pas de verdict (ou rouge) → le gate exige la preuve, jamais de faux-vert.
    diff_text = git.diff_text(sot, base=_BASE_BRANCH, head=feat["branch"])
    ui_touched = verify.has_visual_change(diff_files, diff_text)
    if ui_touched and not verify.status(settings, project, feature, current_sha=sha)["fresh"]:
        with contextlib.suppress(ValueError, OSError):
            verify.autoverify_feature(conn, settings, project=project, feature=feature, sha=sha)
    ev = merge_gate.evaluate_gate(conn, settings, feature_ref=feature_ref, human_go=False, git=git)
    decision = ev.get("decision") or {}
    return {"feature": feature, "merge_ready": bool(decision.get("gate_green")),
            "blockers": list(decision.get("blockers", [])), "review": review_report}


def run_project(conn: sqlite3.Connection, settings: Settings, *, project: str,
                max_parallel: int = DEFAULT_MAX_PARALLEL, git: GitBackend | None = None,
                runner: worker.Runner | None = None,
                review_runner: reviewer.Runner | None = None) -> dict:
    """Draine la roadmap de `project` : découvre les features prêtes, en dispatche jusqu'à `max_parallel` en
    parallèle, avance le DAG au fil des succès, jusqu'à épuisement. Terminaison **garantie** : chaque run
    fini fait progresser une task (`done`) OU exclut sa feature (`failed`) → l'ensemble du travail restant
    décroît strictement. `conn` = connexion principale (lecture roadmap + assignation) ; les workers ouvrent
    la leur. Retourne un rapport agrégé (cf. `_summarize`)."""
    git = git or InternalGit()
    # Auto-heal : clôt un socle DÉJÀ travaillé dont l'interview a été interrompue avant sa clôture (PTY tué) —
    # sinon il resterait tenu en `needs_interview` et bloquerait l'aval. No-op si rien à réconcilier.
    interview.reconcile_socle(conn, settings, project=project, git=git)
    # Gate socle : les features de travail branchent depuis `dev` (WORKTREE_BASE) et ont besoin du design du
    # socle. Tant que le socle n'est pas MERGÉ dans `dev` (GO humain, fail-closed — jamais auto), NE PAS
    # drainer l'aval : un worker sur un `dev` sans design coderait contre le squelette (desync). Le socle
    # lui-même reste dispatchable (sa task interactive est tenue pour `cockpit interview`). C'est
    # l'enforcement inter-feature (le socle est le prérequis implicite de toute feature de travail).
    socle = interview.socle_feature(conn, project)
    socle_slug = socle["slug"] if socle else None
    socle_blocking = socle is not None and socle["status"] != "merged"
    held_for_socle: set[str] = set()   # features de travail tenues jusqu'au merge du socle (pas un échec)
    max_parallel = max(1, max_parallel)
    abort.clear_abort(settings, project)   # run FRAIS : purge une sentinelle éventée d'un run précédent
    aborted = False               # un abort humain (UI/CLI/Ctrl-C) a rompu le drain → run marqué interrompu
    in_flight: set[str] = set()   # features en vol — MUTÉ À LA SOUMISSION (mutex, seul le principal assigne)
    failed: set[str] = set()      # features dont un run a échoué → exclues du reste du run (borne le run)
    needs_interview: set[str] = set()   # v12 : next task interactive → tenue pour le terminal (pas un échec)
    rate_limited: set[str] = set()   # v15 : run rejeté par le plafond 5h org → tenue (pas échec), re-runnable
    rate_hit = False              # rejet rate-limit (org-global) → cesser d'assigner (les autres rejettent)
    interrupted: set[str] = set()   # v16 : run coupé par SIGTERM externe → tenue (pas échec), re-runnable
    reports: list[dict] = []
    pending: dict[Future, str] = {}

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while True:
            # Abort humain (sentinelle posée par `abort.request_abort` : bouton UI, `cockpit abort`, Ctrl-C) :
            # on cesse d'assigner AVANT de découvrir. Les workers en vol ont déjà été tués (killpg) → le
            # `with` les joint sans pendre. On sort proprement : run `aborted` (rien mergé, re-runnable).
            if abort.abort_requested(settings, project):
                aborted = True
                break
            # `failed | needs_interview | held_for_socle` = les features à NE PLUS soumettre (échec, tenue au
            # terminal, ou tenue jusqu'au merge du socle) : sans les exclure, elles resteraient READY et la
            # boucle spinnerait à l'infini.
            for slug in _discoverable_features(conn, project, in_flight,
                                               failed | needs_interview | held_for_socle
                                               | rate_limited | interrupted):
                if socle_blocking and slug != socle_slug:
                    held_for_socle.add(slug)            # feature de travail : attend le merge du socle
                    continue
                if len(in_flight) >= max_parallel:
                    break
                in_flight.add(slug)                     # réservé AVANT submit → jamais deux fois la feature
                pending[pool.submit(_dispatch_one, settings, project, slug, git, runner)] = slug
            if not pending:
                break                                   # rien en vol ni à soumettre → drainé
            done_set, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done_set:
                slug = pending.pop(fut)
                in_flight.discard(slug)
                report = fut.result()                   # _dispatch_one ne lève jamais
                reports.append(report)
                if report.get("needs_terminal"):
                    needs_interview.add(slug)           # tenue pour `cockpit interview` — pas un échec
                elif report.get("rate_limited"):
                    rate_limited.add(slug)              # plafond 5h org → tenue (pas un échec), re-runnable
                    rate_hit = True
                elif report.get("interrupted"):
                    interrupted.add(slug)               # SIGTERM externe → tenue ; le drain CONTINUE (≠ D1 :
                    #                                     l'interruption est par-worker, pas org-globale)
                elif not report["ok"]:
                    failed.add(slug)                    # exclue → pas de re-dispatch en boucle infinie
            if rate_hit:
                break   # plafond org-global : inutile d'assigner d'autres features (rejet garanti)

    abort.clear_abort(settings, project)   # sortie de boucle : la sentinelle a joué son rôle → on la purge
    # Drain fini → FINALISE chaque feature au travail complet : Tier-0 + reviewer dispatché → merge-ready.
    # C'est le tronçon « qualité » de la boucle autonome (le merge reste le GO humain, hors boucle).
    worked = _worked_complete_features(conn, project,
                                       failed | needs_interview | held_for_socle
                                       | rate_limited | interrupted)
    finalizations = [finalize_feature(conn, settings, project, slug, review_runner=review_runner)
                     for slug in worked]
    dispositions = _dispositions(conn, project, drained=set(worked), failed=failed,
                                 interview=needs_interview, held_socle=held_for_socle,
                                 rate_limited=rate_limited, interrupted=interrupted)
    return _summarize(project, reports, failed, finalizations, needs_interview=needs_interview,
                      held_for_socle=held_for_socle, rate_limited=rate_limited, interrupted=interrupted,
                      dispositions=dispositions, aborted=aborted)


def run_feature(conn: sqlite3.Connection, settings: Settings, *, project: str, feature: str,
                git: GitBackend | None = None, runner: worker.Runner | None = None,
                review_runner: reviewer.Runner | None = None) -> dict:
    """Draine le DAG intra-feature d'UNE feature (worker → eager-`done`, SÉQUENTIEL : feature = mutex, 1
    worker à la fois) PUIS la FINALISE (Tier-0 toolchain + reviewer dispatché → gate en preview GO=false).
    Symétrise le chemin WEB (`POST /api/dispatch`) sur le chemin CLI (`run_project`) SANS dupliquer la
    sémantique : réutilise `_dispatch_one` (qui possède la transition `done` — cf. docstring de module),
    `_worked_complete_features` et `finalize_feature`. `conn` = lectures roadmap + finalisation (thread
    appelant) ; chaque worker ouvre la SIENNE (thread-local). Terminaison garantie : chaque run réussi avance
    une task (`done`) ⇒ le READY décroît ; un échec rompt la boucle (task revenue `todo` par `dispatch_next`,
    jamais re-dispatchée en spin). Retourne le rapport agrégé de `_summarize` (même forme que `run_project`,
    donc l'UI et le CLI lisent un rapport identique). `KeyError`/`ValueError` (projet/feature absent)
    remontent au handler global (→ 404)."""
    git = git or InternalGit()
    # Auto-heal : clôt un socle DÉJÀ travaillé dont l'interview a été interrompue avant sa clôture (PTY tué).
    interview.reconcile_socle(conn, settings, project=project, git=git)
    # Gate socle (symétrique de run_project) : une feature de travail ne se draine pas tant que le socle du
    # projet n'est pas MERGÉ dans `dev` — sinon elle branche depuis un `dev` sans design (desync). Le socle
    # lui-même n'est pas gaté (il doit pouvoir être drainé/tenu pour l'interview).
    socle = interview.socle_feature(conn, project)
    if socle is not None and socle["status"] != "merged" and feature != socle["slug"]:
        return _summarize(project, [], set(), [], held_for_socle={feature})
    feature_ref = f"{project}/{feature}"
    abort.clear_abort(settings, project)   # (web) run frais : purge une sentinelle éventée
    aborted = False
    reports: list[dict] = []
    while True:
        # Abort humain (bouton « Arrêter le run » → POST abort sur un autre thread du daemon, ou Ctrl-C) :
        # le worker en vol a déjà été tué (killpg) → on cesse de spinner, run `aborted` (rien mergé).
        if abort.abort_requested(settings, project):
            aborted = True
            break
        index = resolver.index_for_feature(conn, feature_ref)   # KeyError si projet/feature absent
        if not index or resolver.resolve_next(index) is None:
            break                                               # plus de task READY → feature drainée
        report = _dispatch_one(settings, project, feature, git, runner)
        reports.append(report)
        if not report["ok"]:
            break                                               # échec OU interactive : ne PAS spinner
    abort.clear_abort(settings, project)
    last_held = bool(reports and reports[-1].get("needs_terminal"))   # v12 : tenue pour l'interview terminale
    last_rate = bool(reports and reports[-1].get("rate_limited"))     # v15 : rejet rate-limit (pas un échec)
    last_interrupt = bool(reports and reports[-1].get("interrupted"))  # v16 : SIGTERM externe (pas un échec)
    needs_interview: set[str] = {feature} if last_held else set()
    rate_limited: set[str] = {feature} if last_rate else set()
    interrupted: set[str] = {feature} if last_interrupt else set()
    failed: set[str] = ({feature} if (reports and not reports[-1]["ok"] and not last_held and not last_rate
                                      and not last_interrupt) else set())
    worked = _worked_complete_features(conn, project,
                                       failed | needs_interview | rate_limited | interrupted)
    finalizations = ([finalize_feature(conn, settings, project, feature, review_runner=review_runner)]
                     if feature in worked else [])
    dispositions = _dispositions(conn, project, drained=set(worked), failed=failed,
                                 interview=needs_interview, held_socle=set(), rate_limited=rate_limited,
                                 interrupted=interrupted)
    return _summarize(project, reports, failed, finalizations, needs_interview=needs_interview,
                      rate_limited=rate_limited, interrupted=interrupted, dispositions=dispositions,
                      aborted=aborted)


_DISPOSITIONS = ("drained", "interview", "held_socle", "rate_limited", "interrupted", "failed", "blocked")


def _dispositions(conn: sqlite3.Connection, project: str, *, drained: set[str], failed: set[str],
                  interview: set[str], held_socle: set[str],
                  rate_limited: set[str] | None = None,
                  interrupted: set[str] | None = None) -> dict[str, list[str]]:
    """Range CHAQUE feature non-inerte du projet dans **UNE** disposition, sans double-compte — c'est ce qui
    permet un résumé exact (« 1 drainée, 1 tenue-interview, 2 bloquées ») au lieu de « 4 dispatchée, 3 ok »
    qui agrège des cas distincts (bug de lisibilité constaté E2E 2026-07-18). `blocked` = ni drainée, ni
    tenue (interview/socle), ni échouée : elle n'a aucune task READY (DAG inter- ou intra-feature non
    débloqué) → elle n'a rien fait ce run. Les statuts inertes (merged/cancelled) sont hors-run. Read-only."""
    rate_held = rate_limited or set()
    interrupt_held = interrupted or set()
    disp: dict[str, list[str]] = {k: [] for k in _DISPOSITIONS}
    for f in model.list_features(conn, project):
        slug = f["slug"]
        if f["status"] in _INERT_FEATURE_STATUS:
            continue
        if slug in failed:
            disp["failed"].append(slug)
        elif slug in interview:
            disp["interview"].append(slug)
        elif slug in rate_held:
            disp["rate_limited"].append(slug)   # tenue par le plafond 5h org (pas un échec), re-runnable
        elif slug in interrupt_held:
            disp["interrupted"].append(slug)    # tenue par un SIGTERM externe (pas un échec), re-runnable
        elif slug in held_socle:
            disp["held_socle"].append(slug)
        elif slug in drained:
            disp["drained"].append(slug)
        else:
            disp["blocked"].append(slug)   # rien de READY (deps intra/inter non débloqués) → n'a rien fait
    return disp


def _summarize(project: str, reports: list[dict], failed: set[str],
               finalizations: list[dict] | None = None, *,
               needs_interview: set[str] | None = None,
               held_for_socle: set[str] | None = None,
               rate_limited: set[str] | None = None,
               interrupted: set[str] | None = None,
               dispositions: dict[str, list[str]] | None = None,
               aborted: bool = False) -> dict:
    """Agrège les runs + les **finalisations** (Tier-0 + review par feature complète). `drained` (bool) ⟺
    aucune feature en échec ; `merge_ready` = features dont le gate est vert (prêtes au GO humain).
    `needs_interview` (v12) = features dont la next task est `interactive` : tenues pour `cockpit interview`
    (surfacées, pas comptées en échec). `held_for_socle` = features de travail NON dispatchées parce que le
    socle du projet n'est pas encore mergé dans `dev`. `rate_limited` (v15) = features tenues par le plafond
    5 h de l'org (rejet `claude -p`) : ni ok ni échec, re-dispatchables après reset. `interrupted` (v16) =
    features tenues par un SIGTERM externe (teardown/OOM/session) : ni ok ni échec, re-dispatchables. `counts`
    (par `_dispositions`) ventile les features **sans double-compte** — source de vérité lisible
    du résumé (les clés historiques `dispatched`/`ok` restent par-task-run, conservées pour la compat schéma).
    `aborted` = un abort humain a rompu le run (rien mergé, re-runnable)."""
    fins = finalizations or []
    held = needs_interview or set()
    socle_held = held_for_socle or set()
    disp = dispositions or {}
    # Une run interactive apparaît dans `reports` avec ok=False (aucun worker lancé) mais N'EST PAS un échec :
    # on ne la compte ni dans `ok` ni dans `failed`, elle vit dans `needs_interview`.
    rate_held = rate_limited or set()
    interrupt_held = interrupted or set()
    n_ok = sum(1 for r in reports if r["ok"])
    n_held = sum(1 for r in reports if r.get("needs_terminal"))
    n_rate = sum(1 for r in reports if r.get("rate_limited"))   # v15 : ni ok ni échec (plafond 5h org)
    n_interrupt = sum(1 for r in reports if r.get("interrupted"))   # v16 : ni ok ni échec (SIGTERM externe)
    counts = {k: len(disp.get(k, [])) for k in _DISPOSITIONS}
    return {"project": project, "dispatched": len(reports), "ok": n_ok,
            "failed": len(reports) - n_ok - n_held - n_rate - n_interrupt, "failed_features": sorted(failed),
            "drained": not failed, "runs": reports,
            "needs_interview": sorted(held),
            "held_for_socle": sorted(socle_held),
            "rate_limited": sorted(rate_held),
            "interrupted": sorted(interrupt_held),
            "finalizations": fins,
            "merge_ready": sorted(f["feature"] for f in fins if f["merge_ready"]),
            "counts": counts, "blocked_features": sorted(disp.get("blocked", [])),
            "aborted": aborted}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit run <project> [--max-parallel N]` : draine la roadmap en parallèle, imprime le rapport
    (dispatchées / ok / échouées). Exit 0 si drainée sans échec, 1 sinon (une feature en échec, task `todo`
    re-dispatchable — le relancer reprend là où ça a bloqué). **Gate d'auth** : refuse AVANT tout spawn si
    la machine n'a pas d'auth Claude explicite (sinon N features échoueraient en série)."""
    if not auth.claude_auth_status()["authenticated"]:
        print(f"erreur : {auth.AUTH_HINT}")
        return 2
    conn = store.open_db(settings)
    prev_sigint = signal.getsignal(signal.SIGINT)

    def _on_sigint(_signum: int, _frame: object) -> None:
        # Ctrl-C = abort de PREMIÈRE CLASSE (plus de `pkill` fragile) : tue les workers de CE run par leur
        # pgid persisté (ce qui débloque le join du ThreadPoolExecutor) et pose la sentinelle → la boucle
        # sort proprement, run `aborted`. request_abort ouvre sa propre connexion (jamais le `conn` du run).
        print(f"\n⏹  abort demandé (Ctrl-C) — arrêt des workers de {args.project}…")
        abort.request_abort(settings, project=args.project)

    signal.signal(signal.SIGINT, _on_sigint)
    try:
        summary = run_project(conn, settings, project=args.project,
                              max_parallel=getattr(args, "max_parallel", DEFAULT_MAX_PARALLEL))
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        conn.close()
    if summary.get("aborted"):
        print(f"run {args.project} : INTERROMPU (abort humain) — workers arrêtés, mutex/worktree libéré(s), "
              f"rien mergé (fail-closed). Relançable : `cockpit run {args.project}`.")
        return 130
    c = summary["counts"]
    tail = ("roadmap drainée" if summary["drained"]
            else f"features en échec : {', '.join(summary['failed_features'])}")
    parts = [f"{c['drained']} drainée(s)", f"{c['interview']} tenue(s) interview",
             f"{c['blocked']} bloquée(s)", f"{c['failed']} échouée(s)"]
    if c["held_socle"]:
        parts.append(f"{c['held_socle']} en attente socle")
    if c["rate_limited"]:
        parts.append(f"{c['rate_limited']} tenue(s) rate-limit")
    if c["interrupted"]:
        parts.append(f"{c['interrupted']} interrompue(s)")
    print(f"run {args.project} : {', '.join(parts)} — {tail}")
    for feat in summary.get("needs_interview", []):
        print(f"  🖐 {feat} — interview terminale requise (task interactive, non dispatchable en headless) : "
              f"lance `cockpit interview {args.project}` dans un terminal.")
    held_for_socle = summary.get("held_for_socle", [])
    if held_for_socle:
        print(f"  ⏸ {len(held_for_socle)} feature(s) de travail en attente du merge du socle "
              f"(elles branchent depuis dev et ont besoin du design) : {', '.join(held_for_socle)}.")
        print(f"    → merge d'abord le socle (GO humain) : `cockpit merge {args.project}/<socle> --go`, "
              f"puis relance `cockpit run {args.project}`.")
    rate_limited = summary.get("rate_limited", [])
    if rate_limited:
        print(f"  ⏸ {len(rate_limited)} feature(s) tenue(s) — plafond rate-limit 5 h de l'org atteint "
              f"(rien mergé, re-runnable) : {', '.join(rate_limited)}.")
        print(f"    → relance après le reset du plafond : `cockpit run {args.project}` "
              f"(les tasks sont revenues `todo`, reprise là où ça a tenu).")
    interrupted = summary.get("interrupted", [])
    if interrupted:
        print(f"  ⏸ {len(interrupted)} feature(s) interrompue(s) (signal externe : teardown/OOM/session) — "
              f"tenue(s), rien mergé, re-runnable : {', '.join(interrupted)}.")
        print(f"    → relance : `cockpit run {args.project}` (les tasks sont revenues `todo`).")
    for fin in summary["finalizations"]:
        if fin["merge_ready"]:
            print(f"  ✅ {fin['feature']} — MERGE-READY (Tier-0 + review OK) → `cockpit merge {args.project}"
                  f"/{fin['feature']} --go`")
        else:
            why = "; ".join(fin["blockers"][:2]) or "gate incomplet"
            print(f"  🟡 {fin['feature']} — pas encore merge-ready : {why}")
    if summary["merge_ready"]:
        print(f"→ {len(summary['merge_ready'])} feature(s) prête(s) au GO humain : "
              f"{', '.join(summary['merge_ready'])}")
    return 0 if summary["drained"] else 1
