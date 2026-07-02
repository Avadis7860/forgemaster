"""worker — spawn d'un worker `claude` headless (`claude -p`) **LOCAL** dans le worktree de la feature,
sur la NEXT task (séquentiel intra-feature). Gate d'entrée dur : **pas de task, pas de dispatch** (une
feature sans task définie ne peut rien dispatcher).

Port : le write-path de `services/aggregator/routers/orchestrator.py` (POST dispatch), **découpé** hors du
monolithe 1650-LOC (refactor **#3**) et **délocalisé** (refactor **#2** : plus de `ssh dev@ip` — on
exécute via `core.run` dans le worktree local). Enchaînement task→task puis feature « prête au gate ».

Statut : stub. `cli_dispatch` porte `cockpit dispatch <feature>`.
"""
from __future__ import annotations

import argparse

from cockpit.config import Settings

_SRC = "services/aggregator/routers/orchestrator.py (POST dispatch)"


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit dispatch <feature>` : résout la NEXT task, refuse si aucune (gate anti-dispatch),
    réserve le worktree, spawn `claude -p` local. À porter (refactor #2/#3)."""
    raise NotImplementedError(f"port: {_SRC} — #3 (feature={args.feature!r}, gate no-task-no-dispatch)")
