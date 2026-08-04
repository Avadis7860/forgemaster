"""Tests de la couche `design` — application d'un template UI de référence à un projet (« inspire »).

Trois niveaux : (1) unité sur `seed.write_design_seed` (écrit le brief + copie les frères, garde no-op sur
brief blanc, fail-soft si frères absents) ; (2) intégration `apply.apply_template` sur un **SoT réel** (crée
la feature+task de customisation, réserve le worktree, committe la graine ; idempotent ; erreurs typées) ;
(3) HTTP `POST /api/projects/{p}/inspire` via TestClient (201, 404 template/projet inconnus).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgemaster.config import Settings
from forgemaster.daemon import app as app_mod
from forgemaster.db import store
from forgemaster.design import apply, seed
from forgemaster.git.internal import InternalGit
from forgemaster.projects import registry
from forgemaster.roadmap import model

_TOML = """\
[template]
version = "1"
slug = "browser-game-spatial"
name = "Browser-game spatial — deep-space"
tool_type = "browser-game"
genre = "spatial"
intention = "Écran de commandement d'un jeu spatial."
themes = ["orbital", "reactor", "void"]
"""


def _source_template(base: Path, slug: str = "browser-game-spatial", *, siblings: bool = True) -> Path:
    """Un dossier de template SERVI (`<base>/<slug>/`) : `template.toml` + éventuels frères tokens/preview."""
    src = base / "served" / slug
    src.mkdir(parents=True)
    (src / "template.toml").write_text(_TOML, encoding="utf-8")
    if siblings:
        (src / "tokens.css").write_text(":root{--accent:#c60}", encoding="utf-8")
        (src / "preview.png").write_bytes(b"\x89PNG\r\n fake")
    return src


@pytest.fixture
def env(tmp_path: Path, fake_tools, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))               # isolé : git commit n'hérite d'aucun global
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    fake_tools(settings)
    yield settings, conn
    conn.close()


# -- unité : write_design_seed ----------------------------------------------------------------------

def test_write_design_seed_writes_brief_and_copies_siblings(tmp_path: Path):
    src = _source_template(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    brief = seed.write_design_seed(wt, "browser-game-spatial", source_dir=src, brief_text="# Cible\ncorps")
    assert brief is not None and brief.name == "brief.md"
    d = wt / "docs" / "design" / "browser-game-spatial"
    assert (d / "brief.md").read_text(encoding="utf-8").strip() == "# Cible\ncorps"
    assert (d / "tokens.css").is_file() and (d / "preview.png").is_file()   # frères copiés verbatim


def test_write_design_seed_blank_brief_is_noop(tmp_path: Path):
    """Brief blanc → aucune graine (symétrie avec `write_decision_doc` : pas de minerai vide)."""
    src = _source_template(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    assert seed.write_design_seed(wt, "x", source_dir=src, brief_text="   ") is None
    assert not (wt / "docs" / "design").exists()             # rien écrit du tout


def test_write_design_seed_missing_siblings_is_failsoft(tmp_path: Path):
    """Template minimal (sans tokens/preview à la source) → le brief seul est écrit, aucun crash."""
    src = _source_template(tmp_path, siblings=False)
    wt = tmp_path / "wt"
    wt.mkdir()
    brief = seed.write_design_seed(wt, "x", source_dir=src, brief_text="# c")
    assert brief is not None
    d = wt / "docs" / "design" / "x"
    assert (d / "brief.md").is_file() and not (d / "tokens.css").exists()


# -- intégration : apply_template (SoT réel) --------------------------------------------------------

def test_apply_template_creates_feature_task_and_commits_seed(env, tmp_path: Path):
    """Forme A : `inspire` crée la feature+task de customisation ET committe la graine dans son worktree."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    src = _source_template(tmp_path)
    report = apply.apply_template(conn, settings, project="vr",
                                  template_slug="browser-game-spatial", source_dir=src)
    assert report["feature"] == "design-browser-game-spatial" and report["task"] == "customize-ui"
    # feature + task présentes en base
    assert "design-browser-game-spatial" in {f["slug"] for f in model.list_features(conn, "vr")}
    feat = model.resolve_feature(conn, "vr/design-browser-game-spatial")
    assert {t["slug"] for t in model.list_tasks(conn, feat["id"])} == {"customize-ui"}
    # graine écrite + committée dans le worktree
    wt = Path(report["path"])
    d = wt / "docs" / "design" / "browser-game-spatial"
    assert (d / "brief.md").is_file() and (d / "tokens.css").is_file() and (d / "preview.png").is_file()
    assert "browser-game-spatial" in (d / "brief.md").read_text(encoding="utf-8")
    assert set(report["files"]) == {"brief.md", "tokens.css", "preview.png"}
    git = InternalGit()
    assert report["commit"] and git.current_branch(wt) == "feature/design-browser-game-spatial"


def test_apply_template_is_idempotent(env, tmp_path: Path):
    """Ré-inspiration : pas de doublon feature/task, et le 2e commit est no-op (arbre net → commit None)."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    src = _source_template(tmp_path)
    r1 = apply.apply_template(conn, settings, project="vr",
                              template_slug="browser-game-spatial", source_dir=src)
    r2 = apply.apply_template(conn, settings, project="vr",
                              template_slug="browser-game-spatial", source_dir=src)
    feats = [f for f in model.list_features(conn, "vr") if f["slug"] == "design-browser-game-spatial"]
    assert len(feats) == 1                                   # une seule feature
    feat = model.resolve_feature(conn, "vr/design-browser-game-spatial")
    assert [t["slug"] for t in model.list_tasks(conn, feat["id"])] == ["customize-ui"]   # une seule task
    assert r1["commit"] and r2["commit"] is None             # rien de neuf à committer la 2e fois


def test_apply_template_unknown_template_raises_valueerror(env, tmp_path: Path):
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    with pytest.raises(ValueError):
        apply.apply_template(conn, settings, project="vr", template_slug="ghost",
                             source_dir=tmp_path / "nope")


def test_apply_template_unknown_project_raises_keyerror(env, tmp_path: Path):
    settings, conn = env
    src = _source_template(tmp_path)
    with pytest.raises(KeyError):
        apply.apply_template(conn, settings, project="ghost",
                             template_slug="browser-game-spatial", source_dir=src)


# -- HTTP : POST /api/projects/{p}/inspire ----------------------------------------------------------

def test_inspire_endpoint_applies_and_guards(env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`POST /inspire` → 201 + rapport ; template inconnu → 404 ; projet inconnu → 404 (KeyError global)."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    dist = tmp_path / "dist"
    td = dist / "templates" / "browser-game-spatial"
    td.mkdir(parents=True)
    (td / "template.toml").write_text(_TOML, encoding="utf-8")
    (td / "tokens.css").write_text(":root{--accent:#c60}", encoding="utf-8")
    (td / "preview.png").write_bytes(b"\x89PNG\r\n fake")
    monkeypatch.setenv("FORGEMASTER_WEB_DIST", str(dist))        # web_dist_dir() → notre dist factice
    client = TestClient(app_mod.build_app(settings))

    r = client.post("/api/projects/vr/inspire", json={"template": "browser-game-spatial"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["feature"] == "design-browser-game-spatial" and body["task"] == "customize-ui"
    assert set(body["files"]) == {"brief.md", "tokens.css", "preview.png"}
    # gardes : template inconnu → 404 (anti path-traversal via known_template_slugs)
    assert client.post("/api/projects/vr/inspire", json={"template": "ghost"}).status_code == 404
    # projet inconnu → 404 (KeyError d'apply_template mappé globalement)
    assert client.post("/api/projects/ghost/inspire",
                       json={"template": "browser-game-spatial"}).status_code == 404
