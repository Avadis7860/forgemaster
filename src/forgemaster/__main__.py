"""Point d'entrée `python -m forgemaster` → délègue à la CLI (même dispatch que le script `forgemaster`)."""
from __future__ import annotations

from forgemaster.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
