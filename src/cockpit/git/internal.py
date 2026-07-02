"""internal — `InternalGit` : adapter `GitBackend` sur un **repo bare LOCAL** (zéro réseau). C'est
l'implémentation V1 par défaut (décision internal-first).

Port : `services/aggregator/git_ops.py` (builders de commandes purs : status/diff/log/branch/merge +
classification d'erreurs 409/502 + validateurs de branche). Refactor appliqué :
- **#2** : les builders `cd <workdir> && git …` étaient exécutés par `ssh dev@ip` ; ici on exécute via
  `core.run(argv, cwd=workdir)` LOCAL (argv liste, plus de shell).
- **#6** : le `pull` legacy faisait `reset --hard && clean -fd` (destructif) → pull non destructif.
- **#7/#12** : worktree attaché au SoT partagé, `add -B <branch> origin/<base>`, sérialisé par flock sur
  `.git/cockpit-worktree.lock` (spec sot-local-worker-vs-clone-split).

Statut : stub. Signatures figées par `git/backend.py`.
"""
from __future__ import annotations

from pathlib import Path

_SRC = "services/aggregator/git_ops.py"


class InternalGit:
    """Adapter GitBackend sur repo bare local. À porter depuis git_ops.py (refactor #2/#6/#7/#12)."""

    def init_sot(self, sot: Path) -> None:
        raise NotImplementedError(f"port: {_SRC} — #2")

    def add_worktree(self, sot: Path, worktree: Path, *, branch: str, base: str) -> None:
        raise NotImplementedError(f"port: {_SRC} — #7 (worktree attaché au SoT, add -B origin/base)")

    def remove_worktree(self, sot: Path, worktree: Path) -> None:
        raise NotImplementedError(f"port: {_SRC} — #7 (remove AVANT delete_branch)")

    def delete_branch(self, sot: Path, branch: str) -> None:
        raise NotImplementedError(f"port: {_SRC} — #2")

    def current_branch(self, workdir: Path) -> str:
        raise NotImplementedError(f"port: {_SRC} — #2")

    def status(self, workdir: Path) -> dict:
        raise NotImplementedError(f"port: {_SRC} — #2 (parse_status)")

    def merge_ff(self, sot: Path, *, into: str, source: str) -> None:
        raise NotImplementedError(f"port: {_SRC} — #2")

    def merge_writeback(self, sot: Path, *, creds_ref: str | None, identity: tuple[str, str]) -> None:
        raise NotImplementedError("port: services/loops/worker_merge_gate — #8 (creds+identité injectés)")

    def push_mirror(self, sot: Path, remote: str) -> bool:
        raise NotImplementedError(f"port: {_SRC} — #2 (best-effort, ne lève jamais une fois porté)")
