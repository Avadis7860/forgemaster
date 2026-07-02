"""verify — le gate **feature-verified** (Tier-1.5) : prouve que le RÉSULTAT métier s'affiche réellement
(marqueurs attendus dans le DOM + screenshot non vide), pas juste que le déploiement répond 200.

Port : `services/loops/feature_verify.py`. Décisions verrouillées (spec feature-verified-gate) :
porte **déterministe** (le LLM ne juge/seuil pas) ; scope aux **cibles déclarées du diff** (`has_ui()`) ;
**fail-CLOSED** pour l'irréversible (UI touchée + preuve absente/périmée/rouge → merge bloqué) ; ancre =
SHA HEAD (tout commit ultérieur périme) ; **« jamais blanchi »** (node/browser/timeout → ok=False, jamais
vert) ; **N/A-safe** (pas d'UI → aucun blocker ajouté). Override humain **Tier-1.5 seulement** (jamais un
🔴 Tier-0). Refactor **#10** (le gate lui-même) + **#13** (chemins runner/cache/state par config).

Statut : stub. `cli_dispatch` porte `cockpit gate verify <feature>`.
"""
from __future__ import annotations

import argparse

from cockpit.config import Settings

_SRC = "services/loops/feature_verify.py"


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit gate verify <feature>` : gate e2e déterministe, fail-closed, N/A-safe. Porter #10."""
    raise NotImplementedError(f"port: {_SRC} — #10 (feature={args.feature!r})")
