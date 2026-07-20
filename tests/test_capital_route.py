"""Tests de `routes/capital` — l'explorer READ-ONLY du capital-token servi par le MCP.

Aucun réseau : on injecte les seams du router (`browser_factory`, `status_fn`). Prouve (1) `/status` = porte
sans réseau reflétant `wire_state` ; (2) les routes de parcours **passent** la sortie du browser telle quelle
(pass-through — le serveur MCP est la SoT de la forme) ; (3) **503 honnête** quand le browser rend `None` (MCP
non câblé/injoignable) ; (4) un corps `{...collections:[]}` reste une réponse valide (indispo ≠ vide).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cockpit.config import Settings
from cockpit.daemon.deps import Deps
from cockpit.daemon.routes.capital import make_capital_router


class _FakeBrowser:
    """Browser factice : rend `data` pour toute méthode (ou None = indispo). Zéro réseau."""

    def __init__(self, data: object) -> None:
        self._data = data

    def list_types(self) -> object:
        return self._data

    def list_collections(self, type: str) -> object:
        return self._data

    def list_sections(self, type: str, scope: str | None = None) -> object:
        return self._data

    def read(self, type: str, ref: str) -> object:
        return self._data


@pytest.fixture
def make_client(tmp_path: Path):
    def _build(data: object = None, *, wired: bool = True) -> TestClient:
        settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
        app = FastAPI()
        app.state.deps = Deps(settings)
        app.include_router(make_capital_router(
            browser_factory=lambda _s: _FakeBrowser(data),  # type: ignore[arg-type]
            status_fn=lambda: {"wired": wired, "endpoint": "http://mcp.test/mcp"},
        ))
        return TestClient(app)
    return _build


# -- porte : /status sans réseau --------------------------------------------------------------------

def test_status_reflects_wire_state(make_client):
    """`/status` rend `wire_state()` tel quel — la porte que le front lit avant de tenter un parcours."""
    assert make_client(wired=True).get("/api/capital/status").json() == {
        "wired": True, "endpoint": "http://mcp.test/mcp"}
    assert make_client(wired=False).get("/api/capital/status").json()["wired"] is False


# -- parcours : pass-through de la sortie MCP (déjà bien formée) -------------------------------------

def test_types_passes_mcp_body_through(make_client):
    """La forme MCP `{types:[…]}` est servie telle quelle (aucun ré-emballage)."""
    body = {"types": [{"id": "tech", "layout": "silo"}, {"id": "blueprint", "layout": "flat-collection"}]}
    r = make_client(body).get("/api/capital/types")
    assert r.status_code == 200
    assert r.json() == body


def test_collections_and_sections_pass_through(make_client):
    coll = {"type": "tech", "collections": [{"name": "fastapi", "completeness": "full"}], "facets": []}
    c = make_client(coll).get("/api/capital/tech/collections")
    assert c.status_code == 200
    assert c.json() == coll

    # silo : ?scope requis ; le corps MCP porte déjà type/scope/sections/total
    sec = {"type": "tech", "scope": "fastapi", "sections": [{"path": "pages/0002.md", "title": "About"}],
           "total": 153}
    s = make_client(sec).get("/api/capital/tech/sections", params={"scope": "fastapi"})
    assert s.status_code == 200
    assert s.json() == sec


def test_flat_sections_without_scope(make_client):
    """Un type plat (blueprint) liste ses sections **sans** scope (`?scope` omis)."""
    sec = {"type": "blueprint", "scope": None, "sections": [{"id": "typed-corpus-mcp", "title": "…"}],
           "total": 13}
    s = make_client(sec).get("/api/capital/blueprint/sections")
    assert s.status_code == 200
    assert s.json() == sec


def test_read_passes_body_through(make_client):
    """Le corps `read` est servi tel quel (le champ prose diffère par type : `body`/`content`)."""
    body = {"type": "blueprint", "ref": "some-bp", "title": "Blueprint X", "body": "# corps\n…"}
    r = make_client(body).get("/api/capital/read", params={"type": "blueprint", "ref": "some-bp"})
    assert r.status_code == 200
    assert r.json() == body


def test_empty_collections_is_200_not_503(make_client):
    """Un `collections:[]` (type plat) est une réponse valide → 200, PAS un 503 (indispo ≠ vide)."""
    body = {"type": "blueprint", "collections": [], "facets": []}
    r = make_client(body).get("/api/capital/blueprint/collections")
    assert r.status_code == 200
    assert r.json() == body


# -- dégradation honnête : None → 503 ---------------------------------------------------------------

def test_unavailable_browser_is_503_on_all_browse_routes(make_client):
    """Browser None (MCP non câblé/injoignable) → 503 honnête sur chaque route de parcours, jamais un 500."""
    client = make_client(None)   # toute méthode rend None
    assert client.get("/api/capital/types").status_code == 503
    assert client.get("/api/capital/tech/collections").status_code == 503
    assert client.get("/api/capital/tech/sections", params={"scope": "fastapi"}).status_code == 503
    assert client.get("/api/capital/read", params={"type": "tech", "ref": "x"}).status_code == 503


def test_get_is_idempotent(make_client):
    """Deux GET identiques → même réponse : read-only strict, goto-safe pour la boucle visuelle."""
    client = make_client({"types": [{"id": "tech"}]})
    assert client.get("/api/capital/types").json() == client.get("/api/capital/types").json()
