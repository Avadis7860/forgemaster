"""jobs — état des runs worker (table `dispatch_jobs`) + capture/suivi des logs. Un job matérialise un
worker sur une task : pid, port, statut, chemin de log, streamable en live vers le web (P5).

Port : concept de `services/aggregator/routers/dispatch_ws.py` (observateur read-only, garde UUID
anti-injection). Refactor **#5** : le suivi legacy était un one-liner bash `find … | tail -F` fragile
(poll 120×1s) sur transcript distant → ici lecture incrémentale robuste d'un fichier de log **local**
(offset persistant, pas de sous-shell). Observateur 100 % découplé du dispatch (bon pattern conservé).

Statut : stub. Signatures figées.
"""
from __future__ import annotations

from collections.abc import Iterator

from cockpit.config import Settings

_SRC = "services/aggregator/routers/dispatch_ws.py"


def record_start(settings: Settings, *, task_id: str, worktree: str, port: int | None, pid: int) -> str:
    """Insère un `dispatch_job` en statut running, retourne son id. À porter."""
    raise NotImplementedError(f"port: {_SRC} — #5 (record start)")


def tail(settings: Settings, job_id: str) -> Iterator[str]:
    """Suit le log d'un job de façon incrémentale et robuste (offset local, pas de `tail -F` bash —
    refactor #5). À porter."""
    raise NotImplementedError(f"port: {_SRC} — #5 (lecture incrémentale robuste)")
