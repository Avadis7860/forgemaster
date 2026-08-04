#!/usr/bin/env python3
"""front_conformance.py — gate déterministe du design-system front (version exécutable de la doctrine).

Scanne `web/src` et refuse les entorses qui recréent la dette : bouton brut au lieu de la primitive,
z-index / couleur arbitraires au lieu des tokens, teinte de statut inline au lieu de la source unique.
Exempte les primitives (`components/ui/`), les tests et statusTone (la source légitime). Échappatoire par
commentaire `forgemaster:allow` sur la ligne. Retourne un code non nul s'il reste une violation.

Usage : python web/tools/front_conformance.py   (depuis la racine du repo ou n'importe où)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
EXEMPT_DIRS = ("components/ui/",)
EXEMPT_FILES = ("lib/statusTone.ts",)
ALLOW = "forgemaster:allow"

# (id, regex, message) — chaque règle cible une entorse précise à la doctrine.
RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("R1", re.compile(r"<button(\s|>)"), "bouton brut → <Button> (components/ui)"),
    ("R2", re.compile(r"<a\s[^>]*className="), "<a> stylé → <Link> (routing) ou une primitive"),
    ("R3", re.compile(r"\bz-\[", ), "z-index arbitraire → échelle nommée z-(--z-*)"),
    ("R4", re.compile(r"-\[#[0-9a-fA-F]{3,8}\]"), "couleur arbitraire → token @theme"),
    ("R5", re.compile(r"bg-\w+-100\b.*\btext-\w+-800\b"), "teinte de statut inline → lib/statusTone.ts"),
]


def _exempt(rel: str) -> bool:
    return any(d in rel for d in EXEMPT_DIRS) or rel.endswith(EXEMPT_FILES) \
        or rel.endswith((".test.tsx", ".test.ts", ".d.ts"))


def _scan_lines(rel: str, lines: list[str]) -> list[str]:
    """Applique les règles ligne à ligne en IGNORANT les commentaires (bloc `/* */`, ligne `//`, JSDoc `*`) :
    la doctrine cible le CODE JSX, pas la prose. Un commentaire qui cite « <button> » pour expliquer qu'on
    utilise `<Link>` n'est pas une entorse (faux-positif R1 corrigé 2026-07-17)."""
    out: list[str] = []
    in_block = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if in_block:                          # dans un bloc /* … */ ouvert plus haut
            if "*/" in line:
                in_block = False
            continue
        if stripped.startswith("/*"):         # ouverture de bloc (mono- ou multi-ligne)
            if "*/" not in line:
                in_block = True
            continue
        if stripped.startswith(("//", "*")):  # ligne // ou continuation JSDoc *
            continue
        if ALLOW in line:
            continue
        for rid, rx, msg in RULES:
            if rx.search(line):
                out.append(f"{rel}:{i}  [{rid}] {msg}")
    return out


def scan() -> list[str]:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.ts*")):
        rel = str(path.relative_to(SRC.parent))
        if _exempt(rel):
            continue
        violations.extend(_scan_lines(rel, path.read_text(encoding="utf-8").splitlines()))
    return violations


def main() -> int:
    if not SRC.is_dir():
        print(f"[front-conformance] {SRC} absent — rien à vérifier")
        return 0
    violations = scan()
    if violations:
        print("[front-conformance] entorses au design-system (ajoute `forgemaster:allow` si justifié) :")
        for v in violations:
            print("  " + v)
        return 1
    print("[front-conformance] OK — design-system respecté")
    return 0


if __name__ == "__main__":
    sys.exit(main())
