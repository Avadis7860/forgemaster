"""cockpit — forge/orchestrateur local : projet → roadmap (features + tasks DAG) → dispatch worker
`claude` en worktree → gate → merge. Spine = CLI + daemon FastAPI partageant un cœur déterministe.

Édition lightweight WSL (zéro Proxmox/GPU/CT). Réimplémentation propre de l'orchestrateur legacy : on
importe les décisions distillées comme specs (docs/specs/), on ne copie pas le code.
"""
from __future__ import annotations

__version__ = "0.1.0"
