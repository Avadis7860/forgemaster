"""dispatch — le cœur : dispatcher un worker `claude` LOCAL sur la NEXT task, dans un worktree isolé
(mutex), et suivre son job. `worktree` (cycle de vie worktree+port), `worker` (spawn), `jobs` (état+logs)."""
from __future__ import annotations
