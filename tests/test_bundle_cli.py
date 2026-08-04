"""Tests de la surface de gestion des bundles — `forgemaster bundle {list,validate,show,version}`.

On teste `provision.manage.cli_dispatch` en direct (via `argparse.Namespace` + `capsys`, pur et
déterministe) sur le registre réel vendoré, puis sur un registre monkeypatché avec un bundle CASSÉ
(patron `_BUNDLES_DIR` de `test_provision.test_list_valid_types_excludes_broken`). Un dernier test prouve
le câblage argparse (`build_parser` route bien `bundle` → `_h_bundle`)."""
from __future__ import annotations

import argparse

import pytest

from forgemaster.provision import discover_types, manage


def _ns(action: str, **kw) -> argparse.Namespace:
    return argparse.Namespace(action=action, **kw)


def _seed_broken_registry(tmp_path, monkeypatch):
    """Un registre isolé : base `generic` valide + un overlay `broken` (default_facet ∉ facets)."""
    import forgemaster.provision as prov
    meta = tmp_path / "bundles" / "base" / ".forgemaster"
    meta.mkdir(parents=True)
    (meta / "bundle.toml").write_text(
        '[bundle]\nversion = "1"\nproject_type = "generic"\nfacets = ["doc"]\ndefault_facet = "doc"\n',
        encoding="utf-8")
    doc = tmp_path / "bundles" / "base" / ".claude" / "facets" / "doc"
    doc.mkdir(parents=True)
    (doc / "PERSONA.md").write_text("x", encoding="utf-8")
    broken = tmp_path / "bundles" / "types" / "broken" / ".forgemaster"
    broken.mkdir(parents=True)
    (broken / "bundle.toml").write_text(
        '[bundle]\nversion = "1"\nproject_type = "broken"\nfacets = ["doc"]\ndefault_facet = "nope"\n',
        encoding="utf-8")
    monkeypatch.setattr(prov, "_BUNDLES_DIR", tmp_path / "bundles")


# -- list -------------------------------------------------------------------------------------------

def test_list_shows_all_real_types_valid(capsys):
    rc = manage.cli_dispatch(None, _ns("list"))
    assert rc == 0
    out = capsys.readouterr().out
    for t in discover_types():                       # les 5 types vendorés (generic, browser-game, …)
        assert t in out, f"list ne montre pas {t}"
    assert "✗" not in out                            # tous valides → aucun ✗
    assert out.count("✓") == len(discover_types())


def test_list_surfaces_broken_with_reason(tmp_path, monkeypatch, capsys):
    _seed_broken_registry(tmp_path, monkeypatch)
    rc = manage.cli_dispatch(None, _ns("list"))
    assert rc == 0                                   # listing non-fatal : on MONTRE le cassé
    out = capsys.readouterr().out
    assert "generic" in out and "✓" in out           # le valide reste ✓
    assert "broken" in out and "✗" in out            # le cassé est surfacé...
    assert "default_facet" in out                    # ...AVEC sa raison (≠ droppé en silence)


# -- validate ---------------------------------------------------------------------------------------

def test_validate_all_real_registry_is_green(capsys):
    rc = manage.cli_dispatch(None, _ns("validate", type=None))
    assert rc == 0
    out = capsys.readouterr().out
    assert f"{len(discover_types())} valides, 0 invalides" in out


def test_validate_returns_1_when_a_bundle_is_broken(tmp_path, monkeypatch, capsys):
    _seed_broken_registry(tmp_path, monkeypatch)
    rc = manage.cli_dispatch(None, _ns("validate", type=None))
    assert rc == 1                                   # exit non-zéro → utilisable en gate/CI
    out = capsys.readouterr().out
    assert "✗ broken" in out and "1 invalides" in out


def test_validate_single_valid_type(capsys):
    rc = manage.cli_dispatch(None, _ns("validate", type="browser-game"))
    assert rc == 0
    assert "✓ browser-game" in capsys.readouterr().out


def test_validate_single_unknown_type_fails(capsys):
    rc = manage.cli_dispatch(None, _ns("validate", type="rust-cli"))
    assert rc == 1
    assert "✗ rust-cli" in capsys.readouterr().out


# -- show -------------------------------------------------------------------------------------------

def test_show_browser_game_detail(capsys):
    rc = manage.cli_dispatch(None, _ns("show", type="browser-game"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "browser-game" in out
    assert "game-design" in out                      # les 4 facettes, dont game-design
    assert "backend" in out                          # default_facet
    for line in out.splitlines():                    # `fichiers <N>` avec N > 0
        if line.startswith("fichiers"):
            assert int(line.split()[-1]) > 0


def test_show_unknown_type_fails_cleanly(capsys):
    rc = manage.cli_dispatch(None, _ns("show", type="rust-cli"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "✗ rust-cli" in out                        # message lisible, pas de traceback


# -- version ----------------------------------------------------------------------------------------

def test_version_single_type_is_bare_string(capsys):
    rc = manage.cli_dispatch(None, _ns("version", type="browser-game"))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "1"    # string nue, scriptable


def test_version_all_types_one_line_each(capsys):
    rc = manage.cli_dispatch(None, _ns("version", type=None))
    assert rc == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == len(discover_types())


def test_version_unknown_type_fails(capsys):
    rc = manage.cli_dispatch(None, _ns("version", type="rust-cli"))
    assert rc == 1
    assert "✗ rust-cli" in capsys.readouterr().out


# -- câblage argparse -------------------------------------------------------------------------------

def test_cli_wires_bundle_group():
    from forgemaster.cli import _HANDLERS, _h_bundle, build_parser
    assert _HANDLERS["bundle"] is _h_bundle
    ns = build_parser().parse_args(["bundle", "show", "browser-game"])
    assert ns.command == "bundle" and ns.action == "show" and ns.type == "browser-game"
    with pytest.raises(SystemExit):                  # action requise : `bundle` seul est refusé
        build_parser().parse_args(["bundle"])
