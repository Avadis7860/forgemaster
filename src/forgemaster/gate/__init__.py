"""gate — les portes de qualité avant merge : `review` (verdict Tier-1 lié au SHA), `verify` (feature-
verified e2e Tier-1.5), `merge` (merge feature + cleanup worktree + writeback creds injectés). Les tiers
s'AJOUTENT et se couvrent (additif, N/A-safe) ; fail-CLOSED pour l'irréversible (spec feature-verified)."""
from __future__ import annotations
