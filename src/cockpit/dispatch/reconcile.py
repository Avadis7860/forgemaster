"""reconcile — réconciliation des jobs `dispatch_jobs` **orphelins** (worker mort/interrompu qui laisse un
job `running` zombie). Deux appelants :

- **au boot du daemon** (`daemon/app` lifespan) : le dispatch est **synchrone in-process** (le worker
  `claude -p` tourne dans le threadpool de la requête) → **aucun** thread de dispatch ne survit un restart.
  Donc tout `dispatch_jobs.status='running'` observé au démarrage est **interrompu par construction**.
- **dans `worker.dispatch_next`** (garde de finalisation) : une exception inattendue qui échappe au
  `except (ToolPreflightError, RunTimeout)` sauterait `record_finish` → job `running` zombie, daemon
  pourtant vivant. La garde appelle `mark_job_orphan` puis re-propage (fail-loud, jamais avalé).

**Préférer le résultat.** Un job interrompu n'est pas forcément un orphelin : le worker a pu **finir**
(émettre son `result` terminal dans le transcript) alors que seul `record_finish` a manqué (daemon
mort/redémarré entre la fin réelle et la persistance). Avant d'écrire `killed`, on lit donc le log streamé du
job : s'il porte un verdict terminal, on **finalise depuis lui** (`done|failed` + métriques réelles) — la
trace durable ne ment jamais sur un succès (un run réussi lu `killed`/$0 poisonne toute instrumentation
d'outcome aval). Seul un job **sans** verdict (worker réellement tué en cours) devient `killed` — légitime.

L'opération = job `running` → **`done|failed`** (depuis le résultat) **ou** **`killed`** (statuts sanctionnés
par le CHECK `dispatch_jobs.status`) + sa task `in_progress` → `todo` (le vrai débloqueur : le résolveur
re-voit la task READY, la feature redevient dispatchable ; le commit `on_success` suit `record_finish` → sur
un job interrompu il n'a probablement pas tourné, le travail se rejoue). Le worktree et le port NE sont PAS
relâchés : réservations idempotentes (`ports.reserve`/`worktree.reserve`), réutilisées au re-dispatch.

Le WHERE `status='running'` scope chaque écriture → on ne clobbère jamais un job abouti (done/failed/killed).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cockpit.dispatch import jobs


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _terminal_result(log_path: str | None) -> dict | None:
    """Le `parsed` d'un job À PARTIR de son log stream-json, SEULEMENT si le flux porte un événement terminal
    `{"type":"result"}` (le verdict final de `claude -p`). None si le log est absent/illisible OU s'arrête
    AVANT ce verdict (worker réellement tué en cours → orphelin légitime). **Strict** : on ne retombe PAS sur
    le dernier objet quelconque (un transcript coupé en plein `assistant`/`tool_result` ne doit jamais passer
    pour un succès) — d'où le scan explicite d'une ligne `type==result` avant de déléguer l'extraction (usage,
    coût, tours, durée, ET la classification rate-limit qui vit sur une ligne `rate_limit_event` DISTINCTE) à
    `jobs.parse_headless_result` sur le **texte complet** — pas juste la ligne `result`, sinon le
    `rate_limit_event` échapperait (un rejet 5h serait relu `failed` au lieu de `rate_limited`)."""
    if not log_path:
        return None
    p = Path(log_path)
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    has_result = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(o, dict) and o.get("type") == "result":
            has_result = True           # gate strict : un verdict terminal existe (pas un transcript coupé)
    if not has_result:
        return None
    return jobs.parse_headless_result(text)


def mark_job_orphan(conn: sqlite3.Connection, job_id: str) -> str | None:
    """Réconcilie UN job `running` interrompu. **Préfère le résultat** : si le log streamé porte déjà un
    `result` terminal (le worker a FINI ; seul `record_finish` a manqué), le job est finalisé DEPUIS ce
    résultat (`record_finish` → `done|failed` + métriques réelles), et non écrasé en `killed`/NULL. Sinon
    (pas de verdict → worker réellement tué en cours) : `running` → `killed`. Dans les deux cas, la task
    `in_progress` → `todo` (re-dispatchable). Retourne le statut final (`'done'|'failed'|'killed'`) si le job
    était bien `running`, sinon None (déjà finalisé → no-op). Le WHERE `status='running'` garantit qu'un job
    déjà abouti n'est jamais écrasé."""
    row = conn.execute(
        "SELECT task_id, log_path FROM dispatch_jobs WHERE id = ? AND status = 'running'",
        (job_id,)).fetchone()
    if row is None:
        return None
    parsed = _terminal_result(row["log_path"])
    if parsed is not None:
        # Le worker a émis son verdict terminal → finalise depuis lui (done|failed + métriques), pas killed.
        # `record_finish` (garde `WHERE status='running'`, encore vrai ici) pose statut + coût/tours/tokens.
        wall = parsed.get("duration_ms")
        jobs.record_finish(conn, job_id, parsed,
                           wall_s=(wall / 1000) if isinstance(wall, (int, float)) else None)
        # Miroir de la classification de `record_finish` (le retour pilote `abort.py` + la truthiness du
        # collecteur) : un rejet rate-limit est `rate_limited`, pas `failed` (D1×D3).
        final = ("rate_limited" if parsed.get("rate_limited")
                 else "done" if parsed.get("ok") else "failed")
    else:
        conn.execute(
            "UPDATE dispatch_jobs SET status = 'killed', ended_at = ? WHERE id = ? AND status = 'running'",
            (_now(), job_id))
        final = "killed"
    conn.execute(
        "UPDATE tasks SET status = 'todo' WHERE id = ? AND status = 'in_progress'", (row["task_id"],))
    conn.commit()
    return final


def reconcile_orphans(conn: sqlite3.Connection) -> list[str]:
    """Réconcilie TOUS les jobs `running` (appelé au boot du daemon — tout `running` au démarrage est
    interrompu, cf. module docstring). Retourne les ids des jobs effectivement réconciliés (vide = rien à
    faire → boot propre) — qu'ils aient été finalisés depuis leur transcript ou marqués `killed`. Idempotent :
    un 2ᵉ appel ne trouve plus de `running` et rend une liste vide."""
    ids = [r["id"] for r in
           conn.execute("SELECT id FROM dispatch_jobs WHERE status = 'running'").fetchall()]
    return [job_id for job_id in ids if mark_job_orphan(conn, job_id)]
