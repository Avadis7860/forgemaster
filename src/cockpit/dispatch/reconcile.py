"""reconcile — réconciliation des jobs `dispatch_jobs` **orphelins** (worker mort/interrompu qui laisse un
job `running` zombie). Deux appelants :

- **au boot du daemon** (`daemon/app` lifespan) : le dispatch est **synchrone in-process** (le worker
  `claude -p` tourne dans le threadpool de la requête) → **aucun** thread de dispatch ne survit un restart.
  Donc tout `dispatch_jobs.status='running'` observé au démarrage est **orphelin par construction** — on le
  réconcilie sans heartbeat ni sonde de PID (déterministe).
- **dans `worker.dispatch_next`** (garde de finalisation) : une exception inattendue qui échappe au
  `except (ToolPreflightError, RunTimeout)` sauterait `record_finish` → job `running` zombie, daemon
  pourtant vivant. La garde appelle `mark_job_orphan` puis re-propage (fail-loud, jamais avalé).

L'opération = job `running` → **`killed`** (statut sanctionné par le CHECK `dispatch_jobs.status` ; distinct
de `failed`, réservé à un run qui a *rapporté* un échec) + sa task `in_progress` → `todo` (le vrai
débloqueur : le résolveur re-voit la task READY, la feature redevient dispatchable). Le worktree et le port
NE sont PAS relâchés : réservations idempotentes (`ports.reserve`/`worktree.reserve`), réutilisées au
re-dispatch — les relâcher churnerait de l'état stable pour rien.

Le WHERE `status='running'` scope chaque écriture → on ne clobbère JAMAIS un job déjà abouti (done/failed).
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


def mark_job_orphan(conn: sqlite3.Connection, job_id: str) -> bool:
    """Réconcilie UN job orphelin : `running` → `killed` + sa task `in_progress` → `todo`. Retourne True si
    le job était bien `running` (donc réconcilié), False s'il était déjà finalisé (no-op). Le WHERE
    `status='running'` garantit qu'un job déjà abouti (done/failed/killed) n'est jamais écrasé."""
    row = conn.execute(
        "SELECT task_id FROM dispatch_jobs WHERE id = ? AND status = 'running'", (job_id,)).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE dispatch_jobs SET status = 'killed', ended_at = ? WHERE id = ? AND status = 'running'",
        (_now(), job_id))
    conn.execute(
        "UPDATE tasks SET status = 'todo' WHERE id = ? AND status = 'in_progress'", (row["task_id"],))
    conn.commit()
    return True


def reconcile_orphans(conn: sqlite3.Connection) -> list[str]:
    """Réconcilie TOUS les jobs `running` (appelé au boot du daemon — tout `running` au démarrage est
    orphelin, cf. module docstring). Retourne les ids des jobs effectivement réconciliés (vide = rien à
    faire → boot propre). Idempotent : un 2ᵉ appel ne trouve plus de `running` et rend une liste vide."""
    ids = [r["id"] for r in
           conn.execute("SELECT id FROM dispatch_jobs WHERE status = 'running'").fetchall()]
    return [job_id for job_id in ids if mark_job_orphan(conn, job_id)]
