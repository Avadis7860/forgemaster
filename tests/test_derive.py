"""Tests de provision.derive — la projection build-time `template corpus → seed vendoré`. Purs (aucune
écriture) sauf la garde de drift, qui vérifie que l'overlay commité EST en phase avec ses sources."""
from __future__ import annotations

import pytest

from cockpit.provision import derive


def test_check_drift_clean_for_browser_game():
    """GARDE centrale : l'overlay browser-game vendoré est **en phase** avec son template — la décision
    de dérivation ne tient que si le seed ne peut pas re-diverger silencieusement. Si ce test rougit :
    quelqu'un a hand-édité un chemin managé au lieu de `cockpit bundle derive`."""
    assert derive.check_drift("browser-game") == []


def test_derive_reproduces_managed_set_with_jetons():
    """`derive_type` produit le set de fichiers managés attendu ; les jetons **archétype** sont remplis
    (gate/versions), les jetons **projet** restent `{{…}}` (remplis par le worker du projet)."""
    res = derive.derive_type("browser-game")
    assert set(res.files) == {"package.json", "tsconfig.json", "src/shared/schema.ts",
                              "src/shared/schema.test.ts", "src/shared/tick.ts",
                              "src/shared/tick.test.ts", "src/index.ts", "server/index.ts",
                              "web/index.html", "web/main.tsx", "web/App.tsx", "vite.config.ts",
                              "vitest.config.ts", "CLAUDE.md"}
    assert res.template_ref == "browser-game-pve/scaffold"
    pkg = res.files["package.json"]
    assert '"name": "game"' in pkg and '"zod"' in pkg          # archétype rempli (nom valide, dép Zod)
    assert "{{gate_cmd}}" not in pkg and "{{ts_version}}" not in pkg
    assert "{{game_name}}" in res.files["src/index.ts"]        # jeton projet laissé verbatim


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


def test_template_provenance_reads_manifest():
    ref, sha = derive.template_provenance("browser-game")
    assert ref == "browser-game-pve/scaffold" and len(sha) == 64      # sha256 hex
    assert derive.template_provenance("generic") is None             # type non dérivé
