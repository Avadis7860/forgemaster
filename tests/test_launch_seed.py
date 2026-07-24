"""Tests du seed de roadmap de lancement : `create_project` (chemin SEED) sème le socle d'amorçage du
bundle dans le board (DB). Pure seed — l'ordre naît de la graine (`depends_on`), pas du moteur.
Voir tasks/cockpit-launch-roadmap-seed + provision.load_launch_roadmap + roadmap.seed."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.projects import registry
from cockpit.roadmap import model, seed

_GIT_ENV = {"PATH": os.environ.get("PATH", ""),
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


def _board(conn, slug: str) -> dict:
    """Le board {feature_slug: [task_slug, ...]} d'un projet, tasks triées par slug (déterministe)."""
    out = {}
    for f in model.list_features(conn, slug):
        out[f["slug"]] = [t["slug"] for t in model.list_tasks(conn, f["id"])]
    return out


def test_generic_project_seeds_socle(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")            # generic → base seule
    assert _board(conn, "proj") == {"socle": ["cadrage", "decompose"]}
    feat = model.list_features(conn, "proj")[0]
    assert feat["facet"] == "doc"
    tasks = {t["slug"]: t for t in model.list_tasks(conn, feat["id"])}
    assert tasks["decompose"]["depends_on"] == ["cadrage"]          # ordre porté par la graine
    assert all(t["acceptance"] for t in tasks.values())            # DoD binaire partout


def test_ogame_overrides_with_design_socle(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="game", project_type="ogame-rogue-like-pve")
    assert _board(conn, "game") == {"socle-design": ["boucle-eco", "decompose", "interview"]}
    feat = model.list_features(conn, "game")[0]
    assert feat["facet"] == "game-design"
    tasks = {t["slug"]: t for t in model.list_tasks(conn, feat["id"])}
    assert tasks["interview"]["depends_on"] == []
    assert tasks["boucle-eco"]["depends_on"] == ["interview"]
    assert tasks["decompose"]["depends_on"] == ["boucle-eco"]       # chaîne linéaire interview→éco→decompose


def test_seed_is_failsoft_never_rolls_back(ctx, monkeypatch):
    settings, conn = ctx
    # une graine cassée (loader qui lève) ne doit PAS empêcher la création : la row + le SoT survivent.
    monkeypatch.setattr(seed, "load_launch_roadmap",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("graine cassée")))
    registry.create_project(conn, settings, slug="proj")
    assert registry.get_project(conn, "proj")["slug"] == "proj"     # row durable
    assert registry.sot_path_for(settings, "proj").is_dir()         # SoT durable
    assert model.list_features(conn, "proj") == []                  # simplement : rien de semé


def test_adopted_project_is_not_seeded(ctx, tmp_path):
    settings, conn = ctx
    up = tmp_path / "upstream"
    up.mkdir()
    run.run(["git", "init", "-q", "-b", "main", str(up)], env=_GIT_ENV)
    (up / "README.md").write_text("# réel\n", encoding="utf-8")
    run.run(["git", "-C", str(up), "add", "-A"], env=_GIT_ENV)
    run.run(["git", "-C", str(up), "commit", "-q", "-m", "réel"], env=_GIT_ENV)
    registry.create_project(conn, settings, slug="adopted", kind="tool", source_url=str(up))
    assert model.list_features(conn, "adopted") == []              # un projet adopté garde SON historique
