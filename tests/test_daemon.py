"""Tests du daemon : `build_app` importable **sans god-module** (DI explicite sur app.state), routers de
domaine frappés par TestClient (projects / roadmap / dispatch / gate, y compris un merge e2e sur SoT réel),
et le pont PTY **local** (`pty_bridge` sur un shell local, plus de ssh)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cockpit.config import Settings
from cockpit.daemon import app as app_mod
from cockpit.db import store
from cockpit.dispatch import worktree
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.roadmap import model
from cockpit.terminal import pty


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return TestClient(app_mod.build_app(settings)), settings


# -- DI + santé ------------------------------------------------------------------------------------

def test_build_app_has_explicit_di_container_and_health(client):
    c, settings = client
    assert c.app.state.deps.settings == settings        # conteneur DI explicite, pas un global
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# -- projects --------------------------------------------------------------------------------------

def test_projects_crud_over_http(client):
    c, _ = client
    assert c.post("/api/projects", json={"slug": "proj"}).status_code == 201
    assert "proj" in [p["slug"] for p in c.get("/api/projects").json()["projects"]]
    assert c.get("/api/projects/proj").json()["slug"] == "proj"
    assert c.get("/api/projects/nope").status_code == 404          # KeyError → 404 (handler global)
    assert c.post("/api/projects", json={"slug": "proj"}).status_code == 400   # doublon → ValueError → 400


# -- service SPA + CORS ----------------------------------------------------------------------------

def test_spa_served_with_client_side_fallback_when_build_present(client):
    c, _ = client
    if not (app_mod.web_dist_dir() / "index.html").exists():
        pytest.skip("build web/dist absent (mode API pure)")
    root = c.get("/")
    assert root.status_code == 200 and "text/html" in root.headers["content-type"]
    # deep-link rafraîchi (route client-side inconnue du serveur) → index.html, pas 404
    deep = c.get("/void-runner")
    assert deep.status_code == 200 and "text/html" in deep.headers["content-type"]
    # une API inexistante reste un 404 JSON (jamais l'index servi à sa place)
    assert c.get("/api/bogus").status_code == 404


def test_cors_allows_vite_dev_origin(client):
    c, _ = client
    r = c.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


# -- roadmap ---------------------------------------------------------------------------------------

def test_roadmap_features_tasks_and_next(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})
    assert c.post("/api/projects/proj/features", json={"slug": "feat"}).status_code == 201
    assert c.post("/api/features/proj/feat/tasks", json={"slug": "schema"}).status_code == 201
    c.post("/api/features/proj/feat/tasks", json={"slug": "api", "depends_on": ["schema"]})
    nxt = c.get("/api/features/proj/feat/next").json()
    assert nxt["next"]["slug"] == "schema" and nxt["n_tasks"] == 2   # api est bloquée par schema
    rm = c.get("/api/projects/proj/roadmap").json()
    assert rm["features"][0]["slug"] == "feat" and len(rm["features"][0]["tasks"]) == 2


# -- dispatch (gate no-task-no-dispatch, sans spawn) -----------------------------------------------

def test_dispatch_refused_without_task_no_spawn(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})
    c.post("/api/projects/proj/features", json={"slug": "empty"})
    r = c.post("/api/dispatch/proj/empty").json()        # feature sans task → refus AVANT tout spawn
    assert r["dispatched"] is False and "aucune task" in r["reason"]
    assert c.get("/api/jobs/inexistant").status_code == 404


# -- gate + merge e2e (SoT bare réel) --------------------------------------------------------------

def _seed_committed_feature(settings) -> str:
    """Seed un projet/feature/task in_progress + worktree committé (dispatch simulé). Retourne le SHA."""
    conn = store.open_db(settings)
    git = InternalGit()
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug="schema")
    conn.execute("UPDATE tasks SET status = 'in_progress' WHERE slug = 'schema'")
    conn.commit()
    res = worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    (res["path"] / "core.py").write_text("value = 1\n", encoding="utf-8")
    git.commit_worktree(res["path"], message="feat: work",
                        identity=resolve_identity("proj", "dev", role="worker"))
    sha = git.feature_sha(registry.sot_path_for(settings, "proj"), "feature/feat")
    conn.close()
    return sha


def test_gate_review_status_and_merge_over_http(client):
    c, settings = client
    sha = _seed_committed_feature(settings)

    # gate absent avant revue
    st0 = c.get("/api/gate/proj/feat").json()
    assert st0["head_sha"] == sha and st0["review"]["present"] is False

    # écrit le verdict Tier-1 (0 finding) → présent + frais
    assert c.post("/api/gate/proj/feat/review", json={"findings": []}).status_code == 201
    st1 = c.get("/api/gate/proj/feat").json()
    assert st1["review"]["present"] and st1["review"]["fresh"] and not st1["review"]["blocking"]

    # merge sans go → hold (gate vert, pas de mutation)
    hold = c.post("/api/merge/proj/feat", json={"go": False}).json()
    assert hold["merged"] is False and hold["decision"]["gate_green"] is True

    # merge avec go → mergé, feature done
    done = c.post("/api/merge/proj/feat", json={"go": True}).json()
    assert done["merged"] is True and done["merge_sha"] == sha and done["closed_tasks"] == ["schema"]
    git = InternalGit()
    sot = registry.sot_path_for(settings, "proj")
    assert git.feature_sha(sot, "dev") == sha and git.feature_sha(sot, "main") == sha


def test_gate_review_fails_closed_when_feature_never_dispatched(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})
    c.post("/api/projects/proj/features", json={"slug": "feat"})
    # aucune branche (jamais dispatchée) → 422 (jamais un verdict non ancré)
    assert c.post("/api/gate/proj/feat/review", json={"findings": []}).status_code == 422


# -- terminal PTY local ----------------------------------------------------------------------------

def test_resolve_workdir_is_bounded_and_control_parsed(tmp_path):
    settings = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    root = str((tmp_path / "p" / "proj").resolve())
    assert pty.resolve_workdir(settings, "proj") == root
    assert pty.resolve_workdir(settings, "proj", "sub/dir").startswith(root + "/")
    with pytest.raises(ValueError):
        pty.resolve_workdir(settings, "proj", "../../etc")           # traversal refusé
    assert pty.parse_control('{"type":"resize","cols":80,"rows":24}') == (24, 80)
    assert pty.parse_control("pas du json") is None
    assert pty.local_shell_argv() == ["/bin/bash", "-l"]


class _FakeWS:
    """WebSocket minimal pour exercer `pty_bridge` sans réseau : collecte les octets, ne se ferme jamais
    côté client (le PTY termine en premier → FIRST_COMPLETED)."""

    def __init__(self) -> None:
        self.sent = bytearray()
        self.closed = False

    async def send_bytes(self, b: bytes) -> None:
        self.sent.extend(b)

    async def receive(self) -> dict:
        await asyncio.sleep(5)                            # sera annulée quand le PTY finit
        return {"type": "websocket.disconnect"}

    async def close(self) -> None:
        self.closed = True


def test_pty_bridge_runs_local_shell_and_relays_output(tmp_path):
    ws = _FakeWS()
    argv = ["/bin/bash", "-c", "printf COCKPIT-PTY-OK"]
    asyncio.run(pty.pty_bridge(ws, argv, cwd=str(tmp_path)))
    assert b"COCKPIT-PTY-OK" in bytes(ws.sent) and ws.closed is True
