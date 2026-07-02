"""review — verdict de review Tier-1 **lié au SHA de HEAD** : le gate refuse de merger si le verdict est
absent, périmé (`reviewed_sha ≠ HEAD`) ou porte un 🔴 non-overridé.

Port : `services/loops/review_state.py`. Garde déterministe conservée : un finding est rejeté si son
`evidence` verbatim n'apparaît pas dans une ligne AJOUTÉE du diff (fail-closed anti-hallucination).
Refactor **#13** : le chemin d'état legacy `.claude/state/review-gate.json` en dur → sous
`settings.home` (config, pas de chemin projet codé en dur). Tier-1 est **non-overridable** (un 🔴
reviewer bloque même sous GO humain — distinct du Tier-1.5 overridable de `verify`).

Statut : stub. `cli_dispatch` porte `cockpit gate review <feature>`.
"""
from __future__ import annotations

import argparse

from cockpit.config import Settings

_SRC = "services/loops/review_state.py"


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit gate review <feature>` : écrit/évalue le verdict Tier-1 lié au SHA. À porter (#13)."""
    raise NotImplementedError(f"port: {_SRC} — #13 (feature={args.feature!r})")
