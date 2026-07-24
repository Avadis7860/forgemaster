"""Tests de provision.derive — les primitives de projection build-time `template corpus → seed vendoré`.
Purs (aucune écriture) : remplissage de jetons, parse de scaffold, splice de région, chaîne d'étapes."""
from __future__ import annotations

import pytest

from cockpit.provision import derive


def test_fill_jetons_fills_archetype_leaves_project_rejects_unknown():
    body = "a={{gate_cmd}} b={{game_name}} c={{bogus}}"
    with pytest.raises(derive.DeriveError, match="bogus"):
        derive.fill_jetons(body, {"gate_cmd": "tsc"}, allow={"game_name"})
    ok = derive.fill_jetons("a={{gate_cmd}} b={{game_name}}", {"gate_cmd": "tsc"}, allow={"game_name"})
    assert ok == "a=tsc b={{game_name}}"                       # archétype rempli, projet laissé


def test_parse_scaffold_splits_fenced_sections():
    md = ("> blabla\n\n### `package.json` (semé)\n\n```json\n{\"x\": 1}\n```\n\n"
          "### `src/a.ts` (semé)\n\n```typescript\nexport const a = 1;\n```\n\n## Ne pas re-débattre\n- x\n")
    got = dict(derive.parse_scaffold(md))
    assert set(got) == {"package.json", "src/a.ts"}
    assert got["package.json"] == '{"x": 1}'                   # la section h2 n'est pas un fichier


def test_splice_region_replaces_only_between_sentinels():
    host = "avant\n<!-- derived:m:start -->\nVIEUX\n<!-- derived:m:end -->\naprès\n"
    out = derive.splice_region(host, "m", "NEUF")
    assert "NEUF" in out and "VIEUX" not in out
    assert out.startswith("avant\n") and out.endswith("après\n")   # hors-région préservé
    with pytest.raises(derive.DeriveError, match="sentinelles"):
        derive.splice_region("aucune sentinelle", "m", "x")


def test_blueprint_step_chain_derives_from_titles_excluding_e0():
    md = ("## Patron d'étapes\n"
          "- **É0 — Amorçage** : x\n"
          "- **É1 — Modèle de domaine** : y\n"
          "- **É2 — Boucle de tick serveur** : z\n")
    chain = derive.blueprint_step_chain(md)
    assert chain == "É1 Modèle de domaine → É2 Boucle de tick serveur"   # É0 exclu, ordre par numéro
