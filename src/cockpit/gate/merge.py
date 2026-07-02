"""merge — merge d'une feature complète : vérifie les gates (review Tier-1 + verify Tier-1.5), merge la
branche (ff feature→dev, puis dev→main), **cleanup le worktree AVANT `git branch -D`**, writeback avec
creds+identité injectés, met à jour la roadmap in-repo (tasks/feature = done).

Specs : `worktree-cleanup-at-merge` (ordre remove-then-delete) + `merge-writeback-injected-creds-identity`
(refactor **#8** : couture unique partagée bouton↔reconcile ; on passe la **réf** BWS pas le secret ;
injection `GIT_*` le temps du writeback, non persistée ; preuve = signaux serveur). Un merge est un acte
outward irréversible → **feu vert humain**, jamais autonome (spec feature-verified).

Port : le write-path merge de `orchestrator.py` + `services/loops/worker_merge_gate.py`, découpé du
monolithe (refactor #3). Statut : stub. `cli_dispatch` porte `cockpit merge <feature>`.
"""
from __future__ import annotations

import argparse

from cockpit.config import Settings

_SRC = "services/loops/worker_merge_gate.py + orchestrator.py (POST merge)"


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit merge <feature>` : gates → merge ff → cleanup worktree → writeback. À porter (#8)."""
    raise NotImplementedError(f"port: {_SRC} — #8 (feature={args.feature!r})")
