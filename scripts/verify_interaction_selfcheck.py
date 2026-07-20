#!/usr/bin/env python3
"""Self-check hors-CI de la preuve DEUX-TEMPS (jalon jouable) du runner `render_check.js`, sur des fixtures
synthétiques — prouve le MÉCANISME sans dépendre d'un vrai jeu (le substrat `tick-substrate-seed` n'est pas
encore semé). Trois cas, un vrai Chromium via playwright-core vendoré (`deploy/runners/`) :

- `interactive.html`        → « Tour 1 » ABSENT at-rest, PRÉSENT après le clic         → 🟢 ok=True
- `interactive-noop.html`   → le geste ne change rien → « Tour 1 » n'apparaît jamais    → 🔴 after_missing
- `interactive-atrest.html` → « Tour 1 » DÉJÀ présent at-rest → geste non causal         → 🔴 pre_present

Requiert node + playwright-core + un Chromium (`~/.cache/ms-playwright`). Hors-CI (pas dans le gate pytest —
cf. `scripts/e2e_runtime.py`). Lance : `python scripts/verify_interaction_selfcheck.py`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "deploy" / "runners" / "render_check.js"
FIX = REPO / "tests" / "fixtures" / "verify"

MARKERS = ["Compteur", "Jouer un tour"]                    # cadre at-rest
AFTER = ["Tour 1"]                                          # n'existe qu'après le geste
CLICKS = [{"text": "Jouer un tour"}]
CASES = [("interactive.html", True), ("interactive-noop.html", False), ("interactive-atrest.html", False)]


def run_case(fixture: str) -> dict:
    payload = {"url": (FIX / fixture).as_uri(), "markers": MARKERS, "clicks": CLICKS, "after_markers": AFTER}
    p = subprocess.run(["node", str(RUNNER), json.dumps(payload)], capture_output=True, text=True, timeout=60)
    if not p.stdout.strip():
        return {"ok": None, "error": f"runner rc={p.returncode}: {p.stderr.strip()[:200]}"}
    return json.loads(p.stdout)


def main() -> int:
    if not RUNNER.is_file():
        print(f"runner absent : {RUNNER}", file=sys.stderr)
        return 2
    failures = 0
    for fixture, expected_ok in CASES:
        out = run_case(fixture)
        ok = out.get("ok")
        mark = "🟢" if ok == expected_ok else "❌"
        print(f"  {mark} {fixture} : ok={ok} (attendu {expected_ok}) "
              f"after_found={out.get('after_found')} after_missing={out.get('after_missing')} "
              f"pre_present={out.get('pre_present')}{' ' + out['error'] if out.get('error') else ''}")
        if ok != expected_ok:
            failures += 1
    print("self-check :", "🟢 OK" if not failures else f"🔴 {failures} écart(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
