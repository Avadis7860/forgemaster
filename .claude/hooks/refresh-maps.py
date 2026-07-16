#!/usr/bin/env python3
"""refresh-maps.py — hook : garde FRAIS les index de cartes (code-map / docs-map / front-map).

Les index `.codemap/` `.docsmap/` `.frontmap/` sont **dérivés, gitignorés, LOCAUX** (jamais versionnés).
Sans régé câblée ils se **figent** : une session qui explore via `codemap where`/`docsmap where` tombe alors
sur un index périmé — ou **absent** (`ok:false, "index absent"` / `hits:[]`) — au lieu de l'état du code réel
(défaut constaté 2026-07-14 : `.codemap` figé, `.docsmap` jamais bâti). Ce hook les régénère :

  - **SessionStart** → build de tous les index dont l'outil est présent (l'exploration DÉMARRE sur du frais).
  - **PostToolUse (Write|Edit)** → build de l'index du type édité (`.py`→codemap, `docs/**.md`→docsmap,
    `web/**.tsx`→codemap+frontmap) → reste frais pendant la session d'édition.

Chaque build est **idempotent** (le tool skippe par hash si rien n'a changé), **best-effort**, et le hook
**exit 0 TOUJOURS** (il rafraîchit, il n'interrompt jamais). Auto-contenu (stdlib pure — voyage dans le
`.claude/` du repo). Chaque outil est résolu dans `<repo>/.venv/bin` puis le PATH ; un outil absent est
ignoré sans erreur. Reçoit l'événement (JSON) sur stdin.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_ALL_MAPS = ("codemap", "docsmap", "frontmap")


def _repo() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()


def _tool(repo: Path, name: str) -> str | None:
    """Exécutable de l'outil : le venv du repo d'abord (vérité en dev), sinon le PATH. None si absent."""
    cand = repo / ".venv" / "bin" / name
    if cand.is_file() and os.access(cand, os.X_OK):
        return str(cand)
    return shutil.which(name)


def _build(repo: Path, name: str) -> None:
    exe = _tool(repo, name)
    if exe is None:
        return                      # outil non installé → rien à rafraîchir (best-effort)
    # best-effort : un build qui échoue/pend ne bloque JAMAIS l'édition (le hook reste exit 0).
    with contextlib.suppress(Exception):
        subprocess.run([exe, "build", "--root", str(repo)],
                       capture_output=True, text=True, timeout=120)


def _maps_for(fp: Path, repo: Path) -> list[str]:
    """Quelle(s) carte(s) régénérer pour un fichier édité (par type/emplacement). Vide si hors périmètre."""
    try:
        rel = fp.resolve().relative_to(repo)
    except ValueError:
        return []                   # édition hors du repo → aucune carte concernée
    top = rel.parts[0] if rel.parts else ""
    suffix = fp.suffix
    maps: list[str] = []
    if suffix == ".py":
        maps.append("codemap")
    if suffix == ".md" and top == "docs":
        maps.append("docsmap")
    if suffix in (".tsx", ".ts") and top == "web":
        maps += ["codemap", "frontmap"]
    return list(dict.fromkeys(maps))    # dédup, ordre préservé


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:               # noqa: BLE001 — pas de stdin exploitable → défaut sûr (tout rafraîchir)
        event = {}
    repo = _repo()
    file_path = (event.get("tool_input") or {}).get("file_path")
    # SessionStart (ou tout événement sans fichier) → rafraîchit tout ce qui est présent.
    if event.get("hook_event_name") == "SessionStart" or not file_path:
        for name in _ALL_MAPS:
            _build(repo, name)
        return 0
    for name in _maps_for(Path(file_path), repo):
        _build(repo, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
