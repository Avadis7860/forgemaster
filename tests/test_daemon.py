"""Tests du daemon : `build_app` importable **sans god-module** (DI explicite sur app.state), routers de
domaine frappés par TestClient (projects / roadmap / dispatch / gate, y compris un merge e2e sur SoT réel),
et le pont PTY **local** (`pty_bridge` sur un shell local, plus de ssh)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cockpit.config import Settings
from cockpit.daemon import app as app_mod
from cockpit.db import store
from cockpit.dispatch import jobs, worktree
from cockpit.gate import toolchain
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
    # kind (v3) : défaut 'project', 'tool' accepté, hors-enum → 400 (ValueError → handler global)
    assert c.get("/api/projects/proj").json()["kind"] == "project"
    assert c.post("/api/projects", json={"slug": "a-tool", "kind": "tool"}).status_code == 201
    assert c.get("/api/projects/a-tool").json()["kind"] == "tool"
    assert c.post("/api/projects", json={"slug": "bad", "kind": "widget"}).status_code == 400


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
    feat = rm["features"][0]
    assert feat["slug"] == "feat" and len(feat["tasks"]) == 2
    # la roadmap est CLASSÉE : chaque task porte son état DAG, la feature porte son NEXT dispatchable
    states = {t["slug"]: t["state"] for t in feat["tasks"]}
    assert states == {"schema": "READY", "api": "BLOCKED_DEPS"}
    assert feat["next"] == "schema"
    api_task = next(t for t in feat["tasks"] if t["slug"] == "api")
    assert api_task["blockers"] == ["schema (todo)"] and api_task["depends_on"] == ["schema"]


# -- dispatch (gate no-task-no-dispatch, sans spawn) -----------------------------------------------

def test_dispatch_refused_without_task_no_spawn(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})
    c.post("/api/projects/proj/features", json={"slug": "empty"})
    r = c.post("/api/dispatch/proj/empty").json()        # feature sans task → refus AVANT tout spawn
    assert r["dispatched"] is False and "aucune task" in r["reason"]
    assert c.get("/api/jobs/inexistant").status_code == 404


def _seed_job(settings, log_path: str, *, status: str = "running") -> str:
    """Seed projet/feature/task + un `dispatch_job` (sans spawn) pointant sur `log_path`. Rend le job_id."""
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    task = model.add_task(conn, feature_ref="proj/feat", slug="schema")
    job_id = jobs.record_start(conn, task_id=task["id"], worktree="/tmp/wt",
                               session_id="sess", log_path=log_path)
    if status != "running":
        conn.execute("UPDATE dispatch_jobs SET status = ?, num_turns = 3 WHERE id = ?", (status, job_id))
    conn.commit()
    conn.close()
    return job_id


def test_feature_jobs_listing_for_discovery(client):
    c, settings = client
    job_id = _seed_job(settings, "/tmp/none.jsonl")
    r = c.get("/api/dispatch/proj/feat/jobs")
    assert r.status_code == 200
    body = r.json()["jobs"]
    assert len(body) == 1 and body[0]["id"] == job_id
    assert body[0]["task_slug"] == "schema" and body[0]["status"] == "running"
    assert c.get("/api/dispatch/proj/nope/jobs").status_code == 404   # feature absente → 404


def test_dispatch_ws_streams_normalized_transcript_then_terminal_frame(client, tmp_path):
    c, settings = client
    log = tmp_path / "transcript.jsonl"                  # transcript JETABLE (jamais un vrai `claude`)
    log.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Je démarre la task."},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "core.py"}}]}}) + "\n"
        + json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "lu", "is_error": False}]}}) + "\n",
        encoding="utf-8")
    job_id = _seed_job(settings, str(log), status="done")   # terminal → le stream draine puis clôt

    with c.websocket_connect(f"/ws/dispatch/{job_id}") as ws:
        e1 = ws.receive_json()
        assert e1["type"] == "assistant" and "démarre" in e1["text"]
        assert e1["tools"][0] == {"name": "Read", "input_summary": "core.py"}
        e2 = ws.receive_json()
        assert e2["type"] == "tool_result" and e2["results"][0]["ok"] is True
        e3 = ws.receive_json()                              # frame terminale de fin de job
        assert e3["type"] == "job" and e3["status"] == "done" and e3["num_turns"] == 3

    # job inconnu → refus 1008, jamais un flux
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws/dispatch/inexistant") as ws:
        ws.receive_json()


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
    # verdict Tier-0-natif vert (core.py ⇒ toolchain backend applicable) : sinon le gate bloque sur
    # « toolchain non exécutée ». Écrit directement (le vrai run tourne dans la preuve dogfood, pas ici).
    toolchain.write_verdict(settings, "proj", "feat",
                            [{"group": "backend", "name": "ruff", "cmd": "ruff check .",
                              "exit_code": 0, "ok": True}], sha=sha)
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
    # décision composée exposée par le GET (preview GO=false) : gate vert → HOLD, sans muter (core.py ≠ UI)
    assert st1["decision"]["decision"] == "hold" and st1["decision"]["gate_green"] is True
    assert st1["decision"]["human_go"] is False and st1["ui_touched"] is False

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


# -- vue git read-only -----------------------------------------------------------------------------

def test_git_view_read_only_over_http(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})   # SoT neuf : dev + main sur le commit racine seedé
    v = c.get("/api/projects/proj/git")
    assert v.status_code == 200
    body = v.json()
    assert {b["name"] for b in body["branches"]} == {"dev", "main"}
    assert all(b["sha"] and b["subject"] == "root: cockpit seed" for b in body["branches"])
    # dev == main sur un SoT neuf → 0 ahead / 0 behind (aucun merge encore)
    assert body["ahead_behind"] == {"base": "main", "head": "dev", "ahead": 0, "behind": 0}
    assert body["logs"]["dev"][0]["subject"] == "root: cockpit seed"
    # projet inconnu → 404 (handler KeyError global), jamais un demi-état inventé
    assert c.get("/api/projects/ghost/git").status_code == 404


# -- onboarding self-hosted (config-requise + credential par repo) ---------------------------------

def test_patch_project_sets_and_clears_mirror_then_gates_credential(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})                             # créé local-only (0 exigence)
    assert c.get("/api/onboarding").json()["complete"] is True                 # aucun miroir → complet
    # PATCH → rendre GitHub-backed : le miroir apparaît, un token devient requis (onboarding incomplet)
    r = c.patch("/api/projects/proj", json={"mirror_remote": "https://github.com/moi/repo.git"})
    assert r.status_code == 200 and r.json()["mirror_remote"] == "https://github.com/moi/repo.git"
    reqs = {x["project"]: x for x in c.get("/api/onboarding").json()["requirements"]}
    assert reqs["proj"]["needs_credential"] is True and reqs["proj"]["satisfied"] is False
    # retrait du miroir (null) → plus d'exigence
    assert c.patch("/api/projects/proj", json={"mirror_remote": None}).json()["mirror_remote"] is None
    assert c.get("/api/onboarding").json()["complete"] is True
    assert c.patch("/api/projects/ghost", json={"mirror_remote": "x"}).status_code == 404


def test_onboarding_status_and_credential_link_over_http(client):
    c, settings = client
    c.post("/api/projects", json={"slug": "plain"})                            # sans miroir → 0 exigence
    c.post("/api/projects", json={"slug": "mirr", "mirror_remote": "https://github.com/x/y.git"})
    st = c.get("/api/onboarding").json()
    assert st["secret_store"]["backend"] == "file" and st["secret_store"]["ready"] is True
    reqs = {r["project"]: r for r in st["requirements"]}
    assert reqs["mirr"]["needs_credential"] is True and reqs["mirr"]["satisfied"] is False
    assert st["complete"] is False                                             # mirr manque un token

    # lier un token (voie fichier) : la réponse porte la RÉFÉRENCE, jamais le token
    r = c.post("/api/projects/mirr/credential", json={"token": "ghp_SECRET", "label": "gh"})
    assert r.status_code == 200 and "ghp_SECRET" not in r.text
    assert r.json()["credential_ref"]
    assert c.get("/api/onboarding").json()["complete"] is True                 # exigence satisfaite
    assert b"ghp_SECRET" not in settings.db_path.read_bytes()                  # 0 token en DB

    # garde-fous : projet inconnu → 404 ; body vide (ni token ni ref) → 400 (ValueError → handler global)
    assert c.post("/api/projects/ghost/credential", json={"token": "x"}).status_code == 404
    assert c.post("/api/projects/mirr/credential", json={}).status_code == 400

    # délier → réf remise à NULL
    assert c.delete("/api/projects/mirr/credential").status_code == 200
    assert c.get("/api/projects/mirr").json()["credential_ref"] is None


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


# -- fail-loud UI : dist absente → page d'aide, jamais un 404 muet ----------------------------------

def test_missing_ui_serves_loud_placeholder_not_silent_404(tmp_path, monkeypatch):
    # COCKPIT_WEB_DIST pointe un dossier VIDE → aucune dist → placeholder fail-loud.
    monkeypatch.setenv("COCKPIT_WEB_DIST", str(tmp_path / "empty-dist"))
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    c = TestClient(app_mod.build_app(settings))
    # L'API reste pleinement valable.
    assert c.get("/health").status_code == 200
    assert c.post("/api/projects", json={"slug": "p"}).status_code == 201
    # `/api/...` inconnu → 404 JSON (jamais le placeholder à la place d'une API).
    assert c.get("/api/nope").status_code == 404
    # `/` (et non-api) → 200 HTML d'aide qui pointe `cockpit setup` (fail-loud, pas silencieux).
    r = c.get("/")
    assert r.status_code == 200
    assert "cockpit setup" in r.text and "non buildée" in r.text
