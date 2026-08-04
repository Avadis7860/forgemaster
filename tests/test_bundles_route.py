"""Tests de `routes/bundles` — l'explorer READ-ONLY de l'intérieur des bundles (grade la surface P5).

Deux niveaux : (1) unité sur le classifieur de curation `_classify` (taxonomie déterministe, premier match) ;
(2) HTTP via TestClient — arbre + corps servis depuis `load_bundle`, fail-closed (type offert seulement),
404 honnêtes, idempotence goto-safe.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgemaster.config import Settings
from forgemaster.daemon import app as app_mod
from forgemaster.daemon.routes import bundles
from forgemaster.provision import load_bundle


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return TestClient(app_mod.build_app(settings))


# -- classifieur de curation ------------------------------------------------------------------------

def test_classify_taxonomy_puts_quality_first_and_defaults_to_plumbing():
    """La taxonomie met en avant ce qui porte la qualité (méthode, contrat deploy, seed, docs) et relègue la
    plomberie d'outillage en défaut. Premier match, déterministe."""
    assert bundles._classify(".claude/facets/backend/METHOD.md") == bundles.GROUP_METHOD
    assert bundles._classify(".forgemaster/bundle.toml") == bundles.GROUP_DEPLOY
    assert bundles._classify("compose.yaml") == bundles.GROUP_DEPLOY
    assert bundles._classify("Dockerfile") == bundles.GROUP_DEPLOY
    assert bundles._classify("src/index.ts") == bundles.GROUP_SEED
    assert bundles._classify("web/App.tsx") == bundles.GROUP_SEED
    assert bundles._classify("server/prod.ts") == bundles.GROUP_SEED
    assert bundles._classify("docs/design.md") == bundles.GROUP_DOCS
    assert bundles._classify("CLAUDE.md") == bundles.GROUP_DOCS
    # un README niché DANS la source reste avec sa source (seed), pas déporté en docs (premier match src/)
    assert bundles._classify("src/README.md") == bundles.GROUP_SEED
    # un README niché HORS source (ex. .claude/) tombe en docs
    assert bundles._classify(".claude/README.md") == bundles.GROUP_DOCS
    assert bundles._classify("tsconfig.json") == bundles.GROUP_PLUMBING
    assert bundles._classify("package-lock.json") == bundles.GROUP_PLUMBING


# -- arbre ------------------------------------------------------------------------------------------

def test_tree_serves_sorted_files_with_groups_from_load_bundle(client):
    """L'arbre d'un type = les fichiers de `load_bundle` triés, chacun avec son groupe de curation. `generic`
    (base seule) porte au moins CLAUDE.md (docs) et son manifeste .forgemaster (deploy)."""
    r = client.get("/api/bundles/generic/tree")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "generic"
    paths = [f["path"] for f in body["files"]]
    assert paths == sorted(paths)                                   # trié, déterministe
    assert set(paths) == set(load_bundle("generic"))               # vérité = ce que reçoit un seed
    by_path = {f["path"]: f["group"] for f in body["files"]}
    assert by_path["CLAUDE.md"] == bundles.GROUP_DOCS
    assert by_path[".forgemaster/bundle.toml"] == bundles.GROUP_DEPLOY


def test_tree_of_typed_overlay_surfaces_method_and_seed(client):
    """Un type à overlay (browser-game) expose ses facettes (méthode) et sa source runnable (seed) — la
    matière qui sert à juger l'efficacité du bundle avant distribution."""
    r = client.get("/api/bundles/browser-game/tree")
    assert r.status_code == 200
    groups = {f["group"] for f in r.json()["files"]}
    assert bundles.GROUP_METHOD in groups                          # .claude/facets/* présents
    assert bundles.GROUP_SEED in groups                            # src|web|server présents


# -- corps ------------------------------------------------------------------------------------------

def test_file_serves_exact_body_from_bundle(client):
    """Le corps servi = exactement `load_bundle(type)[path]` (aucune lecture FS ad-hoc, pas de traversal)."""
    expected = load_bundle("generic")["CLAUDE.md"]
    r = client.get("/api/bundles/generic/file", params={"path": "CLAUDE.md"})
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == expected
    assert body["path"] == "CLAUDE.md" and body["group"] == bundles.GROUP_DOCS


# -- fail-closed + 404 honnêtes ---------------------------------------------------------------------

def test_unknown_type_is_404_on_both_endpoints(client):
    """On ne parcourt que les types OFFERTS : un type hors registre (ou cassé) → 404, jamais un 500/leak."""
    assert client.get("/api/bundles/nope/tree").status_code == 404
    assert client.get("/api/bundles/nope/file", params={"path": "CLAUDE.md"}).status_code == 404


def test_absent_file_is_404(client):
    """Un chemin absent du bundle dégrade proprement (404), pas de fuite FS ni de crash."""
    r = client.get("/api/bundles/generic/file", params={"path": "../../etc/passwd"})
    assert r.status_code == 404                                    # lookup de clé → traversal impossible


def test_get_is_idempotent(client):
    """Deux GET identiques → même réponse : read-only strict, goto-safe pour la boucle visuelle."""
    a = client.get("/api/bundles/generic/tree").json()
    b = client.get("/api/bundles/generic/tree").json()
    assert a == b
