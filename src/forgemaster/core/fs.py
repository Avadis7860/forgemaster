"""fs — accès fichier borné et JSONL. `safe_path` est le port PUR de `terminal.safe_path` du legacy,
**généralisé** : le legacy bornait au `/home/dev` distant en dur ; ici on borne à une `root` passée
explicitement (correctif : racine par config, pas de chemin d'hôte codé en dur).

JSONL : on scinde sur `"\n"` et **jamais** `splitlines()` — ce dernier coupe aussi sur U+2028/2029/85,
qui sont des caractères légitimes dans une valeur JSON (leçon vault : ne pas corrompre une ligne JSONL).
"""
from __future__ import annotations

import json
import posixpath
from collections.abc import Iterable, Iterator
from pathlib import Path


def safe_path(path: str | None, *, root: str) -> str:
    """Normalise `path` et le BORNE à `root` (anti-traversal). PUR. Un chemin relatif est résolu sous
    `root` ; tout `..` qui sort de `root` → `ValueError`. Vide → `root`."""
    p = (path or "").strip() or root
    if not p.startswith("/"):
        p = posixpath.join(root, p)
    p = posixpath.normpath(p)
    if p != root and not p.startswith(root.rstrip("/") + "/"):
        raise ValueError(f"chemin hors de la racine autorisée : {path!r}")
    return p


def iter_jsonl(text: str) -> Iterator[dict]:
    """Itère les objets JSON d'un texte JSONL. Scinde sur `\\n` uniquement ; ignore les lignes vides."""
    for line in text.split("\n"):
        line = line.strip()
        if line:
            yield json.loads(line)


def read_jsonl(file: str | Path) -> list[dict]:
    """Lit un fichier JSONL → liste d'objets. Fichier absent → liste vide (best-effort lecture)."""
    fp = Path(file)
    if not fp.is_file():
        return []
    return list(iter_jsonl(fp.read_text(encoding="utf-8")))


def write_jsonl(file: str | Path, rows: Iterable[dict]) -> None:
    """Écrit `rows` en JSONL déterministe (clés triées, `\\n` final, ensure_ascii=False)."""
    fp = Path(file)
    fp.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    fp.write_text(body, encoding="utf-8")
