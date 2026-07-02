"""Point d'entrée `python -m cockpit` → délègue à la CLI (même dispatch que le script `cockpit`)."""
from __future__ import annotations

from cockpit.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
