#!/usr/bin/env python3
"""Self-check hors-CI du runner `render_check.js` sur des fixtures synthétiques — prouve les MÉCANISMES du
gate visuel Tier-1.5 sans dépendre d'un vrai jeu. Trois familles, un vrai Chromium via playwright-core
vendoré (`deploy/runners/`, + `pngjs` pour le plancher canvas) :

- **preuve deux-temps** (jalon jouable) : « Tour 1 » absent at-rest, présent après le clic → 🟢 ; geste sans
  effet → 🔴 (after_missing) ; « Tour 1 » déjà là at-rest → 🔴 (pre_present, transition non causale) ;
- **contenu WS tardif** (arrive après networkidle) : SANS `wait_for_text` la capture est vide → 🔴 (faux-rouge
  reproduit) ; AVEC `wait_for_text` le runner sonde jusqu'au repère → 🟢 (l'enforcement METHOD est justifié) ;
- **plancher canvas** : un canvas peint → `non_blank_ok` → 🟢 ; un canvas jamais peint (sidecar présent mais
  rendu vide) → 🔴 (ferme le faux-vert « sidecar ment »).

Requiert node + playwright-core + pngjs + un Chromium (`~/.cache/ms-playwright`). Hors-CI (pas dans le gate
pytest — cf. `scripts/e2e_runtime.py`). Lance : `python scripts/verify_interaction_selfcheck.py`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "deploy" / "runners" / "render_check.js"
FIX = REPO / "tests" / "fixtures" / "verify"

_INTER = {"markers": ["Compteur", "Jouer un tour"], "clicks": [{"text": "Jouer un tour"}],
          "after_markers": ["Tour 1"]}                     # cadre at-rest + geste + marqueur post-geste
_CANVAS = {"selector": "canvas", "non_blank": True}

# (fixture, payload-sans-url, ok attendu)
CASES: list[tuple[str, dict, bool]] = [
    ("interactive.html", _INTER, True),
    ("interactive-noop.html", _INTER, False),
    ("interactive-atrest.html", _INTER, False),
    ("ws-late.html", {"markers": ["Flux prêt"]}, False),                       # sans repère → faux-rouge
    ("ws-late.html", {"markers": ["Flux prêt"], "wait_for_text": "Flux prêt",  # avec repère → 🟢
                      "wait_timeout_ms": 4000}, True),
    ("canvas-live.html", {"markers": ["Tour 1"], "canvas": _CANVAS}, True),    # sidecar + canvas peint
    ("canvas-blank.html", {"markers": ["Tour 1"], "canvas": _CANVAS}, False),  # canvas vide → plancher 🔴
]


def run_case(fixture: str, payload: dict) -> dict:
    body = {"url": (FIX / fixture).as_uri(), **payload}
    p = subprocess.run(["node", str(RUNNER), json.dumps(body)], capture_output=True, text=True, timeout=60)
    if not p.stdout.strip():
        return {"ok": None, "error": f"runner rc={p.returncode}: {p.stderr.strip()[:200]}"}
    return json.loads(p.stdout)


def main() -> int:
    if not RUNNER.is_file():
        print(f"runner absent : {RUNNER}", file=sys.stderr)
        return 2
    failures = 0
    for i, (fixture, payload, expected_ok) in enumerate(CASES):
        out = run_case(fixture, payload)
        ok = out.get("ok")
        mark = "🟢" if ok == expected_ok else "❌"
        label = payload.get("wait_for_text") and " (+wait_for_text)" or ""
        print(f"  {mark} [{i}] {fixture}{label} : ok={ok} (attendu {expected_ok}) "
              f"missing={out.get('missing')} after_missing={out.get('after_missing')} "
              f"pre_present={out.get('pre_present')} canvas={out.get('canvas')}"
              f"{' ' + out['error'] if out.get('error') else ''}")
        if ok != expected_ok:
            failures += 1
    print("self-check :", "🟢 OK" if not failures else f"🔴 {failures} écart(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
