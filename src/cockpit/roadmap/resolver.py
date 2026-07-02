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
from cockpit.db import store
from cockpit.roadmap import model

_SRC = "services/aggregator/lib/vault_tasks.py (task-graph-v1)"


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit task <action>` (add|next). `add` = saisie (data layer) ; `next` = résolveur DAG
    (différé à sa propre couche, refactor #9)."""
    if args.action == "add":
        conn = store.open_db(settings)
        try:
            t = model.add_task(conn, feature_ref=args.feature, slug=args.slug, title=args.title,
                               depends_on=args.depends_on, priority="P1")
            print(f"task créée : {args.feature}/{t['slug']} (priorité {t['priority']})")
        except (ValueError, KeyError) as exc:
            print(f"erreur : {exc}")
            return 1
        finally:
            conn.close()
        return 0
    # action == "next"
    raise NotImplementedError(f"port: {_SRC} — #9 (résolveur DAG next — couche dédiée)")
