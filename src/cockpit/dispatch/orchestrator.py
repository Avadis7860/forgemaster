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

Hors V1 : deps **inter-features** (le cas back→front se résout par le merge vers `dev`) et merge auto (la
boucle ne produit que des commits sur branches feature ; `cockpit merge --go` reste un gate humain séparé).
"""
from __future__ import annotations

import argparse
import sqlite3
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from cockpit import auth
from cockpit.config import Settings
from cockpit.db import store
from cockpit.dispatch import worker
from cockpit.git.backend import GitBackend
from cockpit.git.internal import InternalGit
from cockpit.roadmap import model, resolver

DEFAULT_MAX_PARALLEL = 2   # 2 workers concurrents par défaut (borne prudente ; --max-parallel l'ajuste)

# Features hors-jeu pour le dispatch : déjà mergées ou annulées (plus rien à drainer).
_INERT_FEATURE_STATUS = frozenset({"merged", "cancelled"})


def _discoverable_features(conn: sqlite3.Connection, project: str,
                           in_flight: set[str], failed: set[str]) -> list[str]:
    """Slugs des features **dispatchables maintenant** : ni inertes (merged/cancelled), ni en vol, ni en
    échec, et dont le résolveur DAG expose une NEXT task READY. Triées par le rang de cette NEXT (priorité
    ↑, création ↑, slug) → les priorités hautes soumises d'abord. Read-only (connexion principale)."""
    ranked: list[tuple[tuple, str]] = []
    for f in model.list_features(conn, project):
        slug = f["slug"]
        if f["status"] in _INERT_FEATURE_STATUS or slug in in_flight or slug in failed:
            continue
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


def run_project(conn: sqlite3.Connection, settings: Settings, *, project: str,
                max_parallel: int = DEFAULT_MAX_PARALLEL, git: GitBackend | None = None,
                runner: worker.Runner | None = None) -> dict:
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
    return _summarize(project, reports, failed)


def _summarize(project: str, reports: list[dict], failed: set[str]) -> dict:
    """Agrège les runs : combien dispatchées / ok / échouées, quelles features en échec, et si la roadmap
    est **drainée** — c.-à-d. entièrement avancée **sans échec**. (La boucle ne s'arrête que lorsqu'il ne
    reste rien à soumettre ; le seul reliquat possible est une feature en échec qui a laissé des tasks
    `todo` re-dispatchables → `drained` ⟺ aucune feature en échec.)"""
    n_ok = sum(1 for r in reports if r["ok"])
    return {"project": project, "dispatched": len(reports), "ok": n_ok,
            "failed": len(reports) - n_ok, "failed_features": sorted(failed),
            "drained": not failed, "runs": reports}


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
    return 0 if summary["drained"] else 1
