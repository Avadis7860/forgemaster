"""Tests de `forgemaster.toolsync` (P6) : re-sync pull-only ff d'un outil adopté, sur DB + projects_root
jetables, upstreams = vrais repos locaux (zéro réseau). L'adoption passe par `registry.create_project`
(clone bare, `origin` SANS refspec) → couvre le self-heal du refspec de bout en bout. Le pré-chauffage de
l'index Flow est monkeypatché (on teste la GLUE, pas code-map — best-effort par contrat)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from forgemaster import toolsync
from forgemaster.codemap.index import CodemapError, IndexHandle
from forgemaster.config import Settings
from forgemaster.core import run
from forgemaster.db import store
from forgemaster.projects import registry

_GIT_ENV = {"PATH": os.environ.get("PATH", ""),
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}


def _make_upstream(path: Path) -> Path:
    """Vrai repo git local (upstream d'adoption) : branches main + dev, contenu réel, HEAD sur dev."""
    path.mkdir(parents=True)
    run.run(["git", "init", "-q", "-b", "main", str(path)], env=_GIT_ENV)
    (path / "README.md").write_text("# outil\n", encoding="utf-8")
    run.run(["git", "-C", str(path), "add", "-A"], env=_GIT_ENV)
    run.run(["git", "-C", str(path), "commit", "-q", "-m", "adoption commit"], env=_GIT_ENV)
    run.run(["git", "-C", str(path), "branch", "dev"], env=_GIT_ENV)
    run.run(["git", "-C", str(path), "checkout", "-q", "dev"], env=_GIT_ENV)
    return path


def _advance_upstream(path: Path, branch: str) -> str:
    """Avance `branch` d'un commit sur l'upstream non-bare (simule un travail amont hors forgemaster)."""
    run.run(["git", "-C", str(path), "checkout", "-q", branch], env=_GIT_ENV)
    (path / f"{branch}-work.txt").write_text("upstream work\n", encoding="utf-8")
    run.run(["git", "-C", str(path), "add", "-A"], env=_GIT_ENV)
    run.run(["git", "-C", str(path), "commit", "-q", "-m", f"advance {branch}"], env=_GIT_ENV)
    return run.run(["git", "-C", str(path), "rev-parse", branch], env=_GIT_ENV).stdout.strip()


def _sha(sot: Path, ref: str) -> str:
    return run.run(["git", "-C", str(sot), "rev-parse", ref], env=_GIT_ENV).stdout.strip()


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


def _adopt_tool(conn, settings, slug: str, upstream: Path) -> Path:
    """Adopte `upstream` comme outil (`kind=tool`) via P1 → SoT bare cloné (origin sans refspec).
    **Provenance seule** : pas de `mirror_remote` — c'est `origin` que `sync_tool` re-fetch, et un outil
    adopté n'a aucune destination de push (cf. `bootstrap.run_bootstrap`, schéma v20)."""
    registry.create_project(conn, settings, slug=slug, kind="tool", source_url=str(upstream))
    return registry.sot_path_for(settings, slug)


def _spy_index(recorder: list):
    """Fabrique un faux `ensure_index` qui enregistre son appel et renvoie un handle bidon (code-map non
    requis dans le test)."""
    def _fake(settings, project, sot, *, ref="dev", runner=None):
        recorder.append((project, ref))
        return IndexHandle(project=project, ref=ref, sha="deadbeef", root=Path(sot))
    return _fake


# -- happy path : l'amont a avancé → ff local + pré-chauffe l'index ----------------------------------

def test_sync_tool_fast_forwards_and_prewarms_index(ctx, tmp_path, monkeypatch):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u-codemap")
    sot = _adopt_tool(conn, settings, "code-map", up)
    up_dev = _advance_upstream(up, "dev")
    calls: list = []
    monkeypatch.setattr(toolsync, "ensure_index", _spy_index(calls))
    rep = toolsync.sync_tool(conn, settings, slug="code-map")
    assert rep["slug"] == "code-map" and rep["kind"] == "tool"
    assert rep["fetched"] is True and rep["changed"] is True and rep["blocked"] == []
    assert rep["actions"]["dev"]["action"] == "fast_forward" and rep["actions"]["dev"]["to"] == up_dev
    assert _sha(sot, "dev") == up_dev                    # SoT à origin/dev (DoD)
    assert rep["index_refreshed"] is True and calls == [("code-map", "dev")]  # pré-chauffé sur dev


def test_sync_tool_index_prewarm_is_best_effort(ctx, tmp_path, monkeypatch):
    """code-map absent/en échec → le pré-chauffage ne bloque PAS le sync : le git ff a lieu, seul
    `index_refreshed` retombe False (Flow reconstruira lazy au prochain accès)."""
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u")
    sot = _adopt_tool(conn, settings, "code-map", up)
    up_dev = _advance_upstream(up, "dev")

    def _boom(*a, **k):
        raise CodemapError("code-map introuvable")
    monkeypatch.setattr(toolsync, "ensure_index", _boom)
    rep = toolsync.sync_tool(conn, settings, slug="code-map")
    assert rep["changed"] is True and _sha(sot, "dev") == up_dev   # le git ff a bien eu lieu
    assert rep["index_refreshed"] is False                          # dégradé honnête, non bloquant


def test_sync_tool_no_change_does_not_touch_index(ctx, tmp_path, monkeypatch):
    """Rien n'a bougé amont → `already_synced`, `changed=False`, et l'index n'est PAS pré-chauffé (aucun
    coût inutile : le SHA n'a pas changé, le cache reste valide)."""
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u")
    _adopt_tool(conn, settings, "code-map", up)
    calls: list = []
    monkeypatch.setattr(toolsync, "ensure_index", _spy_index(calls))
    rep = toolsync.sync_tool(conn, settings, slug="code-map")
    assert rep["changed"] is False and rep["actions"]["dev"] == {"action": "already_synced"}
    assert rep["index_refreshed"] is False and calls == []          # non appelé


# -- frontière fail-close : un projet REFUSE la route pull-only --------------------------------------

def test_sync_tool_refuses_project_fail_close(ctx, tmp_path):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="void-runner", kind="project")   # SoT autoritatif
    with pytest.raises(toolsync.NotAToolError, match="est un project"):
        toolsync.sync_tool(conn, settings, slug="void-runner")


def test_sync_tool_unknown_slug_raises_keyerror(ctx):
    settings, conn = ctx
    with pytest.raises(KeyError):
        toolsync.sync_tool(conn, settings, slug="fantome")


# -- read-only strict : jamais de push d'un local en avance -----------------------------------------

def test_sync_tool_never_pushes_local_ahead(ctx, tmp_path, monkeypatch):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u")
    sot = _adopt_tool(conn, settings, "code-map", up)
    up_dev_before = _sha(up, "dev")
    # avance LOCALE du SoT (anomalie pour un outil read-only) via un worktree jetable
    wt = tmp_path / "wt"
    run.run(["git", "-C", str(sot), "worktree", "add", "-q", str(wt), "dev"], env=_GIT_ENV)
    (wt / "local.txt").write_text("local\n", encoding="utf-8")
    run.run(["git", "-C", str(wt), "add", "-A"], env=_GIT_ENV)
    run.run(["git", "-C", str(wt), "commit", "-q", "-m", "local"], env=_GIT_ENV)
    run.run(["git", "-C", str(sot), "worktree", "remove", "--force", str(wt)], env=_GIT_ENV)
    monkeypatch.setattr(toolsync, "ensure_index", _spy_index([]))
    rep = toolsync.sync_tool(conn, settings, slug="code-map")
    assert rep["actions"]["dev"]["action"] == "local_ahead_skipped"
    assert rep["changed"] is False and _sha(up, "dev") == up_dev_before   # amont JAMAIS poussé


# -- dégradation honnête : amont injoignable --------------------------------------------------------

def test_sync_tool_degrades_when_unreachable(ctx, tmp_path):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u")
    sot = _adopt_tool(conn, settings, "code-map", up)
    run.run(["git", "-C", str(sot), "remote", "set-url", "origin",
             str(tmp_path / "n-existe-pas.git")], env=_GIT_ENV)
    rep = toolsync.sync_tool(conn, settings, slug="code-map")
    assert rep["fetched"] is False and rep["state"] == "unreachable"
    assert rep["changed"] is False and rep["actions"] == {}


# -- cli_dispatch : codes de sortie honnêtes --------------------------------------------------------

def _args(slug: str) -> argparse.Namespace:
    return argparse.Namespace(slug=slug, home=None, projects_root=None)


def test_cli_dispatch_exit_codes(ctx, tmp_path, monkeypatch, capsys):
    settings, conn = ctx
    conn.close()   # cli_dispatch ouvre sa propre connexion
    up = _make_upstream(tmp_path / "u")
    conn2 = store.open_db(settings)
    _adopt_tool(conn2, settings, "code-map", up)
    registry.create_project(conn2, settings, slug="my-proj", kind="project")
    conn2.close()
    monkeypatch.setattr(toolsync, "ensure_index", _spy_index([]))
    # à jour → 0
    assert toolsync.cli_dispatch(settings, _args("code-map")) == 0
    # avance amont → ff → 0
    _advance_upstream(up, "dev")
    assert toolsync.cli_dispatch(settings, _args("code-map")) == 0
    # projet → fail-close → 1
    assert toolsync.cli_dispatch(settings, _args("my-proj")) == 1
    # introuvable → 1
    assert toolsync.cli_dispatch(settings, _args("fantome")) == 1
    out = capsys.readouterr().out
    assert "ff depuis l'amont" in out and "ne cible que les outils" in out
