"""worktree — cycle de vie d'un worktree de feature : réservation, mutex, port couplé, teardown. Un
worktree = **le mutex** d'une feature (1 worker à la fois) ; N features ⇒ N worktrees parallèles.

Specs : `worktree-cleanup-at-merge` (cleanup worktree AVANT `git branch -D` ; port↔worktree couplé,
relâché au merge ET au reset) + `sot-local-worker-vs-clone-split` (worktree attaché au SoT partagé ;
concurrence sérialisée par **flock** sur `.git/cockpit-worktree.lock`, refactor **#12** — pas un lock
in-process qui ne couvre pas des process concurrents).

Port : concept du broker de ports `services/aggregator/routers/devserver.py` (port_store + locks
partagés) + `worktree_dispatch.py`. Statut : stub. Signatures figées (consommées par `worker`/`gate.merge`).
"""
from __future__ import annotations

from pathlib import Path

from cockpit.config import Settings
from cockpit.git.backend import GitBackend

_SRC = "services/aggregator/routers/devserver.py (port broker) + worktree_dispatch.py"


def reserve(settings: Settings, git: GitBackend, *, project: str, feature: str) -> Path:
    """Réserve (idempotent) un worktree + un port pour `feature`, attaché au SoT. Retourne son chemin.
    Sérialisé par flock sur le `.git` partagé (refactor #12)."""
    raise NotImplementedError(f"port: {_SRC} — #7/#12")


def release(settings: Settings, git: GitBackend, *, project: str, feature: str) -> None:
    """Teardown : `remove_worktree` PUIS relâche le port (spec worktree-cleanup). Appelé au merge ET au
    reset — jamais mono-chemin. Idempotent."""
    raise NotImplementedError(f"port: {_SRC} — #7 (remove worktree AVANT delete_branch ; relâche le port)")


def audit(settings: Settings) -> list[dict]:
    """Audit d'orphelins : worktree-sans-port / port-fantôme / worktree-sur-task-close. Doit rester à 0
    après merge et après reset (spec worktree-cleanup)."""
    raise NotImplementedError(f"port: {_SRC} — #7 (audit orphelins)")
