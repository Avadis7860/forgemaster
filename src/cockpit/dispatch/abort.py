"""abort — arrêt de **première classe** d'un run en cours (drain), depuis l'UI ou la CLI, sans jamais de
`ssh`/`pct exec`/`pkill` manuel (footgun `kill -0` vécu en Phase 4 E2E : `pgrep` a matché sa propre ligne
→ mauvais groupe tué). L'arrêt est **sûr par construction** : il cible chaque worker par son **handle
explicite** (pgid persisté en DB au spawn — `jobs.record_pid`), pas une heuristique de nom de process.

Trois chemins, **un seul mécanisme** :
- **web** — le POST abort arrive sur un autre thread du même daemon → tue les pgids (enfants du daemon) ;
- **CLI cross-process** — `cockpit abort <projet>` (autre process) lit les pgids en DB, les tue ;
- **CLI Ctrl-C** — `cli_dispatch` rattrape le `KeyboardInterrupt` et appelle le même `request_abort`.

Deux effets, indépendants et idempotents :
1. **sentinelle fichier** `home/abort/<project>` — signal cross-process que la boucle de drain (`run_project`
   /`run_feature`) lit **entre deux passes** pour s'arrêter d'assigner. Effacée par la boucle à sa sortie.
2. **teardown** — `killpg(SIGTERM)` → grâce → `killpg(SIGKILL)` sur chaque worker, puis `mark_job_orphan`
   (running→`killed`, task→`todo`) + `error='aborted by human'`. `killed` est déjà sanctionné par le CHECK
   `dispatch_jobs.status` (zéro bump de schéma) ; l'intention humaine est portée par `error`. Worktree/port
   **non relâchés** : réservations idempotentes réutilisées au re-run (cf. `reconcile`) ⇒ **re-runnable**.

Le `killer` est **injectable** (invariant forge : I/O injectable) → testable sans tuer de vrai process.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from cockpit.db import store
from cockpit.dispatch import reconcile

if TYPE_CHECKING:
    from cockpit.config import Settings

# Signature du tueur : (pgid, signal) -> None. Défaut = groupe de process (le worker est spawné
# `start_new_session` → pid == pgid). Injecté en test pour observer les appels sans tuer.
Killer = Callable[[int, int], None]

_ABORTED_REASON = "aborted by human"


def _abort_dir(settings: Settings) -> Path:
    return settings.home / "abort"


def _sentinel(settings: Settings, project: str) -> Path:
    return _abort_dir(settings) / project


def abort_requested(settings: Settings, project: str) -> bool:
    """True si un abort a été demandé pour `project` (sentinelle présente). Lu par la boucle de drain entre
    deux passes → elle cesse d'assigner de nouvelles features et sort proprement."""
    return _sentinel(settings, project).exists()


def clear_abort(settings: Settings, project: str) -> None:
    """Efface la sentinelle (appelé par la boucle de drain à sa sortie) — idempotent."""
    with contextlib.suppress(FileNotFoundError):
        _sentinel(settings, project).unlink()


def _running_jobs(conn: sqlite3.Connection, project: str, feature: str | None) -> list[dict]:
    """Jobs `running` du projet (join task→feature→project), avec leur `pid`. Filtre `feature` si fourni."""
    sql = ("SELECT j.id AS job_id, j.pid AS pid, f.slug AS feature FROM dispatch_jobs j "
           "JOIN tasks t ON j.task_id = t.id JOIN features f ON t.feature_id = f.id "
           "JOIN projects p ON f.project_id = p.id "
           "WHERE p.slug = ? AND j.status = 'running'")
    params: list[object] = [project]
    if feature is not None:
        sql += " AND f.slug = ?"
        params.append(feature)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _kill_groups(pgids: list[int], killer: Killer, *, grace_s: float) -> None:
    """SIGTERM tout le monde → une seule grâce → SIGKILL les survivants. Un process déjà mort
    (`ProcessLookupError`) est ignoré (course normale : le worker a pu finir entre-temps)."""
    for pgid in pgids:
        with contextlib.suppress(ProcessLookupError):
            killer(pgid, signal.SIGTERM)
    if pgids and grace_s > 0:
        time.sleep(grace_s)
    for pgid in pgids:
        with contextlib.suppress(ProcessLookupError):
            killer(pgid, signal.SIGKILL)


def request_abort(settings: Settings, *, project: str, feature: str | None = None,
                  killer: Killer = os.killpg, grace_s: float = 3.0,
                  conn: sqlite3.Connection | None = None) -> dict:
    """Arrête le run en cours de `project` (ou d'une seule `feature`). Pose la sentinelle, tue chaque worker
    par son pgid persisté, marque les jobs `killed` + tasks `todo` + `error`. Idempotent (re-appel = 0 job à
    tuer). `conn` fourni (route daemon) est réutilisé ; sinon on ouvre une connexion propre (CLI/handler).
    Retourne `{project, feature, aborted: n, jobs: [{job_id, feature, pid}]}`."""
    _abort_dir(settings).mkdir(parents=True, exist_ok=True)
    _sentinel(settings, project).touch()   # signal AVANT le kill : la boucle ne re-dispatche pas ce qu'on tue
    owns_conn = conn is None
    conn = conn or store.open_db(settings)
    try:
        jobs_running = _running_jobs(conn, project, feature)
        _kill_groups([j["pid"] for j in jobs_running if j.get("pid")], killer, grace_s=grace_s)
        for job in jobs_running:
            if reconcile.mark_job_orphan(conn, job["job_id"]):
                conn.execute("UPDATE dispatch_jobs SET error = ? WHERE id = ?",
                             (_ABORTED_REASON, job["job_id"]))
                conn.commit()
        return {"project": project, "feature": feature, "aborted": len(jobs_running),
                "jobs": [{"job_id": j["job_id"], "feature": j["feature"], "pid": j["pid"]}
                         for j in jobs_running]}
    finally:
        if owns_conn:
            conn.close()


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit abort <project> [--feature <slug>]` : arrête proprement le run en cours (workers tués
    par leur pgid, mutex/worktree libéré, jobs `killed`, tasks `todo`) — sans `ssh`/`pct`/`pkill`. Idempotent
    (0 worker en vol = déjà arrêté). Exit 0."""
    feature = getattr(args, "feature", None)
    result = request_abort(settings, project=args.project, feature=feature)
    scope = f"{args.project}/{feature}" if feature else args.project
    if result["aborted"]:
        print(f"abort {scope} : {result['aborted']} worker(s) arrêté(s) — mutex/worktree libéré(s), rien "
              f"mergé (fail-closed). Relançable : `cockpit run {args.project}`.")
    else:
        print(f"abort {scope} : aucun run en cours (0 worker) — rien à arrêter.")
    return 0
