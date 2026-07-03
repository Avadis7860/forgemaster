"""Tests de `webbuild` — localisation de `web/` et fail-loud quand Node est absent. On ne lance JAMAIS npm
en test (lent, réseau) : on prouve le câblage et le message actionnable, pas le build lui-même (couvert par
l'acceptance CT vierge)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cockpit import webbuild


def test_find_web_dir_locates_source_checkout():
    # Depuis ce module (tests/), on doit remonter jusqu'au `web/` du repo (porte package.json).
    web = webbuild.find_web_dir(Path(__file__))
    assert web is not None
    assert (web / "package.json").is_file()
    assert web.name == "web"


def test_find_web_dir_none_when_no_sources(tmp_path: Path):
    # Arbre sans `web/package.json` (simule une install wheel pure) → None.
    assert webbuild.find_web_dir(tmp_path) is None


def test_build_front_fails_loud_without_node(tmp_path: Path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text("{}")
    monkeypatch.setattr(webbuild.shutil, "which", lambda _name: None)  # Node absent
    with pytest.raises(webbuild.FrontBuildError) as exc:
        webbuild.build_front(web)
    assert "Node" in str(exc.value) and "cockpit setup" in str(exc.value)
