"""resolver — le résolveur DAG des tasks : classe chaque task (READY/BLOCKED_DEPS/DEFERRED/CYCLE/ERROR)
et calcule la **NEXT** task dispatchable d'une feature. Le graphe est la **seule autorité de
séquencement** (spec task-next-resolver-dag) ; zéro LLM, déterministe, read-only.

Port : `services/aggregator/lib/vault_tasks.py` (moteur `task-graph-v1`) + concept
`lib/task_resolver_proto.py` (héritage de priorité transitif `eff_prio` + ordre total topo — à absorber).
Refactor **#9** : `phases:` (inter-feature) augmente `depends_on` en **union** (jamais écrasement),
dérivé en UN point (fin de load) ; classification à ordre figé ; triggers = grammaire fermée fail-soft
(condition non vérifiable → DEFERRED, jamais READY ni crash) ; date injectée (déterminisme).

Statut : stub. `cli_dispatch` porte `task add|next`.
"""
from __future__ import annotations

import argparse

from cockpit.config import Settings

_SRC = "services/aggregator/lib/vault_tasks.py (task-graph-v1)"


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit task <action>` (add|next). `next` = résolveur DAG. À porter (refactor #9)."""
    raise NotImplementedError(f"port: {_SRC} — #9 (action={args.action!r})")
