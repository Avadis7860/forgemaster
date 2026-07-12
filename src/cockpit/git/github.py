"""github — `GitHubGit` : adapter `GitBackend` sur GitHub (clone, PR flow, PAT étroit gitignored). ⏸
DIFFÉRÉ (P6). En V1, `InternalGit` (bare local) est le seul backend requis ; GitHub reste transport/
miroir best-effort (spec forge-sot-local). Le choix de backend est par-projet (colonne `projects.backend`).

Statut : scaffoldé, non implémenté. Signatures figées par `git/backend.py`.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


class GitHubGit:
    """Adapter GitBackend GitHub. ⏸ Différé P6 — voir docs/specs/forge-code-merge-sot-local.md."""

    def init_sot(self, sot: Path) -> None:
        raise NotImplementedError("différé (P6) : backend GitHub — GitBackend interface figée")

    def add_worktree(self, sot: Path, worktree: Path, *, branch: str, base: str) -> None:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def remove_worktree(self, sot: Path, worktree: Path) -> None:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def delete_branch(self, sot: Path, branch: str) -> None:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def current_branch(self, workdir: Path) -> str:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def status(self, workdir: Path) -> dict:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def merge_ff(self, sot: Path, *, into: str, source: str) -> None:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def merge_writeback(self, sot: Path, *, creds_ref: str | None, identity: tuple[str, str]) -> None:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def push_mirror(self, sot: Path, remote: str) -> bool:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def remote_divergence(
        self, sot: Path, *, remote: str, branches: Sequence[str], creds_ref: str | None = None
    ) -> dict:
        raise NotImplementedError("différé (P6) : backend GitHub — écart SoT↔remote")

    def feature_sha(self, sot: Path, ref: str) -> str:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def diff_names(self, sot: Path, *, base: str, head: str) -> list[str]:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def diff_text(self, sot: Path, *, base: str, head: str) -> str:
        raise NotImplementedError("différé (P6) : backend GitHub")

    def commit_worktree(self, worktree: Path, *, message: str, identity: tuple[str, str]) -> str | None:
        raise NotImplementedError("différé (P6) : backend GitHub")
