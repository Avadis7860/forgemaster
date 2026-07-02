#!/usr/bin/env python3
"""post-edit-check.py — hook PostToolUse (Write|Edit) : valide à la volée le fichier édité.

Auto-contenu (aucune dépendance au vault) : pensé pour vivre dans le `.claude/` d'un repo qui voyage.
  .py            → py_compile (syntaxe) + `ruff check` sur le fichier (si ruff dispo, best-effort)
  .json          → parse
  .toml          → parse (tomllib, Python ≥3.11)

Signale dans le contexte si invalide. **exit 0 TOUJOURS** (non bloquant) : il signale, il n'interrompt
jamais une session. Reçoit l'événement PostToolUse en JSON sur stdin.
"""
from __future__ import annotations

import json
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path


def _check_py(p: Path) -> str | None:
    try:
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as e:
        return f"syntaxe: {e.msg.splitlines()[0] if e.msg else e}"
    ruff = shutil.which("ruff")
    if ruff:
        r = subprocess.run([ruff, "check", "--quiet", str(p)], capture_output=True, text=True)
        if r.returncode != 0:
            first = (r.stdout or r.stderr).strip().splitlines()
            if first:
                return f"ruff: {first[0]}"
    return None


def _check_json(p: Path) -> str | None:
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — best-effort, on ne remonte jamais
        return f"json: {e}"
    return None


def _check_toml(p: Path) -> str | None:
    try:
        import tomllib
        tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return f"toml: {e}"
    return None


_CHECKS = {".py": _check_py, ".json": _check_json, ".toml": _check_toml}


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    fp = (event.get("tool_input") or {}).get("file_path")
    if not fp:
        return 0
    p = Path(fp)
    if not p.is_file():
        return 0
    check = _CHECKS.get(p.suffix)
    if check is None:
        return 0
    err = check(p)
    if err:
        print(f"⚠ post-edit-check — {fp} : {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
