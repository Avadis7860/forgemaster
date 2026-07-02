"""model — la roadmap in-repo `.cockpit/roadmap.yaml` : features + tasks ordonnées, versionnée AVEC le
projet (source de vérité côté repo), synchronisée vers la DB (index du cockpit).

Nouveau (pas de port direct) mais adossé au **contrat data v2** des tasks vault
(`decisions/meta/2026-06-19--tasks-data-layer-refonte.md`) : `depends_on` explicite + `phases:` sur
l'umbrella. Schéma figé — cf. docs/schema-contract.md (§ roadmap.yaml).

Statut : stub. `cli_dispatch` porte `roadmap add-feature|show`.
"""
from __future__ import annotations

import argparse

from cockpit.config import Settings

_SRC = "docs/schema-contract.md (§ roadmap.yaml) + tasks-data-layer-refonte"


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit roadmap <action>` (add-feature|show). À porter (schéma roadmap.yaml figé)."""
    raise NotImplementedError(f"port: {_SRC} — #9 (action={args.action!r})")
