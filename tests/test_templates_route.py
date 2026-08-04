"""Tests de `routes/templates` — la vitrine READ-ONLY des templates de référence UI.

Deux niveaux : (1) unité sur `_scan` (scan `dist/templates/*/template.toml`, fail-closed silencieux sur tout
dossier mal formé) ; (2) HTTP via TestClient — la liste servie depuis `web_dist_dir()` (surchargée par
`FORGEMASTER_WEB_DIST` vers un dist factice), idempotence goto-safe, vide honnête sans build.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgemaster.config import Settings
from forgemaster.daemon import app as app_mod
from forgemaster.daemon.routes import templates

_VALID_TOML = """\
[template]
version = "1"
slug = "browser-game-spatial"
name = "Browser-game spatial — deep-space"
tool_type = "browser-game"
genre = "spatial"
tags = ["browser-game", "spatial", "deep-space"]
intention = "Écran de commandement d'un jeu spatial."
entry = "index.html"
preview = "preview.png"
themes = ["orbital", "reactor", "void"]
"""


def _make_template(base: Path, slug: str, toml: str | None) -> None:
    """Crée `base/templates/<slug>/` (avec `template.toml` si `toml` fourni)."""
    d = base / "templates" / slug
    d.mkdir(parents=True)
    if toml is not None:
        (d / "template.toml").write_text(toml, encoding="utf-8")


# -- scan (unité, fail-closed) ----------------------------------------------------------------------

def test_scan_projects_valid_template(tmp_path: Path):
    """Un dossier avec un `[template]` valide → un résumé complet ; slug = nom de dossier (URL-safe)."""
    _make_template(tmp_path, "browser-game-spatial", _VALID_TOML)
    got = templates._scan(tmp_path)
    assert len(got) == 1
    t = got[0]
    assert t["slug"] == "browser-game-spatial"
    assert t["name"] == "Browser-game spatial — deep-space"
    assert t["tool_type"] == "browser-game" and t["genre"] == "spatial"
    assert t["tags"] == ["browser-game", "spatial", "deep-space"]
    assert t["entry"] == "index.html" and t["preview"] == "preview.png"
    assert t["themes"] == ["orbital", "reactor", "void"]


def test_scan_ignores_malformed_dirs(tmp_path: Path):
    """Fail-closed silencieux : dossier sans TOML, TOML cassé, ou sans table `[template]` → exclus, jamais
    une erreur. Seul le dossier valide survit."""
    _make_template(tmp_path, "valid", _VALID_TOML)
    _make_template(tmp_path, "no-toml", None)                       # pas de template.toml
    _make_template(tmp_path, "broken", "this is = not : valid toml [[[")
    _make_template(tmp_path, "no-table", 'name = "sans table template"')
    slugs = [t["slug"] for t in templates._scan(tmp_path)]
    assert slugs == ["valid"]


def test_scan_slug_is_dir_name_not_toml(tmp_path: Path):
    """Le slug canonique = le nom de dossier, PAS le `slug` du TOML (source d'URL sûre, pas de confiance en
    une valeur de manifeste pour bâtir un chemin)."""
    _make_template(tmp_path, "real-dir-name", _VALID_TOML)          # le TOML dit slug=browser-game-spatial
    assert templates._scan(tmp_path)[0]["slug"] == "real-dir-name"


def test_scan_empty_when_no_templates_dir(tmp_path: Path):
    """Racine sans sous-dossier `templates/` (dev sans build) → liste vide, jamais d'erreur."""
    assert templates._scan(tmp_path) == []


def test_scan_is_sorted(tmp_path: Path):
    """Ordre déterministe (trié par slug) — la grille de vitrine est stable at-rest."""
    _make_template(tmp_path, "zeta", _VALID_TOML)
    _make_template(tmp_path, "alpha", _VALID_TOML)
    assert [t["slug"] for t in templates._scan(tmp_path)] == ["alpha", "zeta"]


# -- HTTP (TestClient) ------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_template(dist, "browser-game-spatial", _VALID_TOML)
    monkeypatch.setenv("FORGEMASTER_WEB_DIST", str(dist))              # web_dist_dir() → notre dist factice
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return TestClient(app_mod.build_app(settings))


def test_list_serves_scanned_templates(client: TestClient):
    """`GET /api/templates` sert la liste scannée depuis le dist servi."""
    r = client.get("/api/templates")
    assert r.status_code == 200
    body = r.json()
    assert [t["slug"] for t in body["templates"]] == ["browser-game-spatial"]
    assert body["templates"][0]["genre"] == "spatial"


def test_list_is_idempotent(client: TestClient):
    """Deux GET identiques → même réponse : read-only strict, goto-safe pour la boucle visuelle."""
    assert client.get("/api/templates").json() == client.get("/api/templates").json()
