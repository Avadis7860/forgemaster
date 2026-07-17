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
import sqlite3
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from cockpit import auth
from cockpit.config import Settings
from cockpit.db import store
from cockpit.dispatch import reviewer, worker
from cockpit.dispatch import worktree as worktree_mod
from cockpit.gate import merge as merge_gate
from cockpit.gate import toolchain
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
                "reason": report.get("reason", "?")}
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


def _finalize_feature(conn: sqlite3.Connection, settings: Settings, project: str, feature: str, *,
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
        toolchain.write_verdict(settings, project, feature, results, sha=sha)
    review_report = reviewer.dispatch_reviewer(conn, settings, feature_ref=feature_ref, git=git,
                                               runner=review_runner)
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
    max_parallel = max(1, max_parallel)
    in_flight: set[str] = set()   # features en vol — MUTÉ À LA SOUMISSION (mutex, seul le principal assigne)
    failed: set[str] = set()      # features dont un run a échoué → exclues du reste du run (borne le run)
    reports: list[dict] = []
    pending: dict[Future, str] = {}

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while True:
            for slug in _discoverable_features(conn, project, in_flight, failed):
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
                if not report["ok"]:
                    failed.add(slug)                    # exclue → pas de re-dispatch en boucle infinie

    # Drain fini → FINALISE chaque feature au travail complet : Tier-0 + reviewer dispatché → merge-ready.
    # C'est le tronçon « qualité » de la boucle autonome (le merge reste le GO humain, hors boucle).
    finalizations = [_finalize_feature(conn, settings, project, slug, review_runner=review_runner)
                     for slug in _worked_complete_features(conn, project, failed)]
    return _summarize(project, reports, failed, finalizations)


def run_feature(conn: sqlite3.Connection, settings: Settings, *, project: str, feature: str,
                git: GitBackend | None = None, runner: worker.Runner | None = None,
                review_runner: reviewer.Runner | None = None) -> dict:
    """Draine le DAG intra-feature d'UNE feature (worker → eager-`done`, SÉQUENTIEL : feature = mutex, 1
    worker à la fois) PUIS la FINALISE (Tier-0 toolchain + reviewer dispatché → gate en preview GO=false).
    Symétrise le chemin WEB (`POST /api/dispatch`) sur le chemin CLI (`run_project`) SANS dupliquer la
    sémantique : réutilise `_dispatch_one` (qui possède la transition `done` — cf. docstring de module),
    `_worked_complete_features` et `_finalize_feature`. `conn` = lectures roadmap + finalisation (thread
    appelant) ; chaque worker ouvre la SIENNE (thread-local). Terminaison garantie : chaque run réussi avance
    une task (`done`) ⇒ le READY décroît ; un échec rompt la boucle (task revenue `todo` par `dispatch_next`,
    jamais re-dispatchée en spin). Retourne le rapport agrégé de `_summarize` (même forme que `run_project`,
    donc l'UI et le CLI lisent un rapport identique). `KeyError`/`ValueError` (projet/feature absent)
    remontent au handler global (→ 404)."""
    git = git or InternalGit()
    feature_ref = f"{project}/{feature}"
    reports: list[dict] = []
    while True:
        index = resolver.index_for_feature(conn, feature_ref)   # KeyError si projet/feature absent
        if not index or resolver.resolve_next(index) is None:
            break                                               # plus de task READY → feature drainée
        report = _dispatch_one(settings, project, feature, git, runner)
        reports.append(report)
        if not report["ok"]:
            break                                               # échec : task revenue `todo` → NE PAS spinner
    failed: set[str] = {feature} if (reports and not reports[-1]["ok"]) else set()
    finalizations = ([_finalize_feature(conn, settings, project, feature, review_runner=review_runner)]
                     if feature in _worked_complete_features(conn, project, failed) else [])
    return _summarize(project, reports, failed, finalizations)


def _summarize(project: str, reports: list[dict], failed: set[str],
               finalizations: list[dict] | None = None) -> dict:
    """Agrège les runs + les **finalisations** (Tier-0 + review par feature complète). `drained` ⟺ aucune
    feature en échec ; `merge_ready` = features dont le gate est vert (prêtes au GO humain). La boucle ne
    s'arrête que lorsqu'il ne reste rien à soumettre ; une feature en échec laisse des tasks `todo`
    re-dispatchables."""
    fins = finalizations or []
    n_ok = sum(1 for r in reports if r["ok"])
    return {"project": project, "dispatched": len(reports), "ok": n_ok,
            "failed": len(reports) - n_ok, "failed_features": sorted(failed),
            "drained": not failed, "runs": reports,
            "finalizations": fins,
            "merge_ready": sorted(f["feature"] for f in fins if f["merge_ready"])}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit run <project> [--max-parallel N]` : draine la roadmap en parallèle, imprime le rapport
    (dispatchées / ok / échouées). Exit 0 si drainée sans échec, 1 sinon (une feature en échec, task `todo`
    re-dispatchable — le relancer reprend là où ça a bloqué). **Gate d'auth** : refuse AVANT tout spawn si
    la machine n'a pas d'auth Claude explicite (sinon N features échoueraient en série)."""
    if not auth.claude_auth_status()["authenticated"]:
        print(f"erreur : {auth.AUTH_HINT}")
        return 2
    conn = store.open_db(settings)
    try:
        summary = run_project(conn, settings, project=args.project,
                              max_parallel=getattr(args, "max_parallel", DEFAULT_MAX_PARALLEL))
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    tail = ("roadmap drainée" if summary["drained"]
            else f"features en échec : {', '.join(summary['failed_features'])}")
    print(f"run {args.project} : {summary['dispatched']} dispatchée(s), {summary['ok']} ok, "
          f"{summary['failed']} échouée(s) — {tail}")
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
