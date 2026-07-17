"""Gate design-system front (`web/tools/front_conformance.py`) — le check ne doit PAS matcher une entorse
citée dans un COMMENTAIRE (faux-positif R1 : un JSDoc qui écrit « pas un `<button>` » n'est pas un bouton
brut), mais doit toujours attraper une entorse dans le CODE."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_FC = Path(__file__).resolve().parents[1] / "web" / "tools" / "front_conformance.py"
_spec = importlib.util.spec_from_file_location("front_conformance", _FC)
assert _spec and _spec.loader
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


def test_r1_ignore_le_button_cite_dans_un_jsdoc():
    # exactement le cas AccueilTab:107 — une continuation JSDoc qui explique l'usage de <Link>.
    lines = [
        "/** Une tuile-lien stylée comme une carte (navigation, pas action, donc un <Link> porte la recette",
        " *  de carte directement — pas un `<button>`). Le badge d'état est optionnel. */",
        "function ScentTile() { return null }",
    ]
    assert fc._scan_lines("src/pages/AccueilTab.tsx", lines) == []


def test_r1_attrape_un_bouton_brut_dans_le_code():
    lines = ["export function X() {", "  return <button className=\"px-2\">go</button>", "}"]
    out = fc._scan_lines("src/pages/X.tsx", lines)
    assert len(out) == 1 and "[R1]" in out[0] and ":2" in out[0]


def test_bloc_commentaire_multiligne_est_ignore():
    lines = [
        "const x = 1",
        "/*",
        "  historiquement on utilisait un <button> ici — plus maintenant",
        "*/",
        "  return <button>vrai</button>",  # hors bloc → doit rester une entorse
    ]
    out = fc._scan_lines("src/y.tsx", lines)
    assert len(out) == 1 and ":5" in out[0]


def test_ligne_commentaire_simple_est_ignoree():
    assert fc._scan_lines("src/z.tsx", ["  // TODO: remplacer le vieux <button> legacy"]) == []
