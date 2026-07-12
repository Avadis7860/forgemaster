"""Tests du daemon : `build_app` importable **sans god-module** (DI explicite sur app.state), routers de
domaine frappés par TestClient (projects / roadmap / dispatch / gate, y compris un merge e2e sur SoT réel),
et le pont PTY **local** (`pty_bridge` sur un shell local, plus de ssh)."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cockpit.config import Settings
from cockpit.core import run
from cockpit.daemon import app as app_mod
from cockpit.db import store
from cockpit.dispatch import jobs, worktree
from cockpit.gate import toolchain
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import InternalGit, writeback_env
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
    # index.html JAMAIS mis en cache heuristiquement (sinon un redeploy montre l'ancienne app) → no-cache
    assert "no-cache" in root.headers.get("cache-control", "")
    # deep-link rafraîchi (route client-side inconnue du serveur) → index.html, pas 404
    deep = c.get("/void-runner")
    assert deep.status_code == 200 and "text/html" in deep.headers["content-type"]
    assert "no-cache" in deep.headers.get("cache-control", "")
    # une API inexistante reste un 404 JSON (jamais l'index servi à sa place)
    assert c.get("/api/bogus").status_code == 404


def test_hashed_assets_served_immutable(client):
    """Les assets hashés (content-addressed) sont servis `immutable` : un rebuild change l'URL, jamais de
    stale. Contrepartie du `no-cache` de l'index — le couple qui tue le « redeploy mais vieille app »."""
    c, _ = client
    assets = app_mod.web_dist_dir() / "assets"
    if not assets.is_dir():
        pytest.skip("build web/dist absent (mode API pure)")
    name = next((p.name for p in assets.iterdir() if p.is_file()), None)
    if name is None:
        pytest.skip("aucun asset hashé dans le build")
    r = c.get(f"/assets/{name}")
    assert r.status_code == 200
    assert "immutable" in r.headers.get("cache-control", "")


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

def test_dispatch_refused_without_task_no_spawn(client, monkeypatch):
    c, _ = client
    # auth Claude présente (déterministe, indépendant du host CI) → on teste le gate no-task, pas l'auth
    monkeypatch.setattr("cockpit.auth.claude_auth_status",
                        lambda *a, **k: {"authenticated": True, "source": "test"})
    c.post("/api/projects", json={"slug": "proj"})
    c.post("/api/projects/proj/features", json={"slug": "empty"})
    r = c.post("/api/dispatch/proj/empty").json()        # feature sans task → refus AVANT tout spawn
    assert r["dispatched"] is False and "aucune task" in r["reason"]
    assert c.get("/api/jobs/inexistant").status_code == 404


def test_dispatch_refused_without_claude_auth(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr("cockpit.auth.claude_auth_status",
                        lambda *a, **k: {"authenticated": False, "source": None})
    c.post("/api/projects", json={"slug": "proj"})
    c.post("/api/projects/proj/features", json={"slug": "feat"})
    r = c.post("/api/dispatch/proj/feat")                # pas d'auth → 403 AVANT tout spawn
    assert r.status_code == 403 and "claude login" in r.json()["detail"]


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


def test_git_sync_endpoint_reports_divergence_and_degrades(client, tmp_path: Path):
    """`GET .../git/sync` (réseau, séparé du `/git` idempotent) rend l'écart SoT↔miroir par branche + rollup,
    avec dégradation honnête : pas de miroir → `no_mirror` ; miroir en avance → `remote_ahead`. Jamais un
    faux-vert 0/0 ni un demi-état inventé (projet absent → 404)."""
    c, settings = client
    env = writeback_env(("T", "t@example.invalid"))

    # projet SANS miroir → dégradation honnête `no_mirror` (fetched=False, rien à comparer, on le DIT)
    c.post("/api/projects", json={"slug": "bare"})
    s0 = c.get("/api/projects/bare/git/sync")
    assert s0.status_code == 200
    assert s0.json() == {"project": "bare", "remote": "mirror", "fetched": False,
                         "branches": {}, "state": "no_mirror"}

    # projet AVEC miroir câblé (clone du SoT → histoire partagée) → `synced` au départ
    c.post("/api/projects", json={"slug": "proj"})
    mirror = tmp_path / "mirror.git"
    assert run.run(["git", "clone", "--bare", "-q",
                    str(registry.sot_path_for(settings, "proj")), str(mirror)]).ok
    assert c.patch("/api/projects/proj", json={"mirror_remote": str(mirror)}).status_code == 200
    s1 = c.get("/api/projects/proj/git/sync").json()
    assert s1["state"] == "synced" and s1["fetched"] is True
    assert s1["branches"]["dev"] == {"ahead": 0, "behind": 0, "state": "synced"}

    # le miroir prend de l'avance sur `dev` (travail hors cockpit) → `remote_ahead` visible sans faux-vert
    wt = tmp_path / "mwt"
    assert run.run(["git", "-C", str(mirror), "worktree", "add", "-q", str(wt), "dev"], env=env).ok
    (wt / "x.txt").write_text("ahead\n", encoding="utf-8")
    assert run.run(["git", "-C", str(wt), "add", "-A"], env=env).ok
    assert run.run(["git", "-C", str(wt), "commit", "-q", "-m", "ahead"], env=env).ok
    s2 = c.get("/api/projects/proj/git/sync").json()
    assert s2["branches"]["dev"]["behind"] == 1 and s2["branches"]["dev"]["state"] == "remote_ahead"
    assert s2["branches"]["main"]["state"] == "synced" and s2["state"] == "remote_ahead"

    # projet inconnu → 404 (handler KeyError global), jamais un demi-état inventé
    assert c.get("/api/projects/ghost/git/sync").status_code == 404


def test_git_tree_and_blob_read_only_over_http(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})   # SoT neuf : arbre racine = payload auto-travaillable
    # arbre racine : dossiers d'abord (.claude, docs) puis les blobs du payload
    t = c.get("/api/projects/proj/git/tree", params={"ref": "dev"})
    assert t.status_code == 200
    body = t.json()
    assert body["ref"] == "dev" and body["path"] == ""
    entries = {e["name"]: e for e in body["entries"]}
    assert entries[".claude"]["type"] == "tree" and entries[".claude"]["size"] is None
    assert entries["CLAUDE.md"]["type"] == "blob" and entries["CLAUDE.md"]["size"] > 0
    types = [e["type"] for e in body["entries"]]
    assert types == sorted(types, key=lambda t: t != "tree")  # tous les arbres avant les blobs
    # descente dans un sous-dossier
    sub = c.get("/api/projects/proj/git/tree", params={"ref": "dev", "path": ".claude"})
    assert sub.status_code == 200 and "settings.json" in {e["name"] for e in sub.json()["entries"]}
    # contenu d'un fichier texte
    b = c.get("/api/projects/proj/git/blob", params={"ref": "dev", "path": "CLAUDE.md"})
    assert b.status_code == 200
    blob = b.json()
    assert blob["binary"] is False and blob["too_large"] is False and blob["content"]
    # réf/chemin introuvable → 404 ; blob sur un dossier → 404 ; projet absent → 404
    assert c.get("/api/projects/proj/git/tree", params={"ref": "nope"}).status_code == 404
    assert c.get("/api/projects/proj/git/blob",
                 params={"ref": "dev", "path": "docs"}).status_code == 404
    assert c.get("/api/projects/ghost/git/tree", params={"ref": "dev"}).status_code == 404


def test_git_intelligence_commit_diff_history_over_http(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})   # SoT neuf : dev == main sur « root: cockpit seed »
    head = c.get("/api/projects/proj/git").json()["logs"]["dev"][0]["sha"]

    # détail du commit racine : métadonnées + fichiers touchés (le payload auto-travaillable)
    d = c.get(f"/api/projects/proj/git/commit/{head}")
    assert d.status_code == 200
    detail = d.json()
    assert detail["subject"] == "root: cockpit seed" and detail["author"] == "cockpit"
    paths = {f["path"] for f in detail["files"]}
    assert "CLAUDE.md" in paths and all("additions" in f and "binary" in f for f in detail["files"])
    assert c.get("/api/projects/proj/git/commit/deadbeef").status_code == 404

    # diff de feature : dev == main sur un SoT neuf → diff vide (200, pas une erreur)
    df = c.get("/api/projects/proj/git/diff", params={"base": "main", "head": "dev"})
    assert df.status_code == 200 and df.json()["diff"] == "" and df.json()["files"] == []
    assert c.get("/api/projects/proj/git/diff",
                 params={"base": "main", "head": "nope"}).status_code == 404

    # historique d'un fichier du payload → au moins le commit racine ; fichier inconnu → liste vide (200)
    h = c.get("/api/projects/proj/git/history", params={"ref": "dev", "path": "CLAUDE.md"})
    assert h.status_code == 200 and h.json()["commits"][0]["subject"] == "root: cockpit seed"
    ghost = c.get("/api/projects/proj/git/history", params={"ref": "dev", "path": "nope.txt"})
    assert ghost.status_code == 200 and ghost.json()["commits"] == []
    assert c.get("/api/projects/proj/git/history",
                 params={"ref": "nope", "path": "CLAUDE.md"}).status_code == 404
    assert c.get("/api/projects/ghost/git/commit/abc").status_code == 404


def test_adopt_repo_over_http_shows_real_code(client, tmp_path):
    """Adoption par l'API (`POST source_url`, chemin public sans credential) → le SoT porte le VRAI code du
    repo, pas le seed. Feature-verified via l'explorateur git (le résultat s'affiche)."""
    c, _ = client
    genv = {"PATH": os.environ.get("PATH", ""), "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}
    up = tmp_path / "upstream"
    up.mkdir()
    run.run(["git", "init", "-q", "-b", "dev", str(up)], env=genv)
    (up / "hello.py").write_text("print('adopted')\n", encoding="utf-8")
    run.run(["git", "-C", str(up), "add", "-A"], env=genv)
    run.run(["git", "-C", str(up), "commit", "-q", "-m", "real"], env=genv)

    r = c.post("/api/projects", json={"slug": "adopted-tool", "kind": "tool", "source_url": str(up)})
    assert r.status_code == 201 and r.json()["source_url"] == str(up) and r.json()["kind"] == "tool"
    # l'explorateur git montre le VRAI fichier (dev+main normalisés) — pas le toolkit semé
    t = c.get("/api/projects/adopted-tool/git/tree", params={"ref": "dev"})
    assert t.status_code == 200
    names = {e["name"] for e in t.json()["entries"]}
    assert "hello.py" in names and "CLAUDE.md" not in names
    b = c.get("/api/projects/adopted-tool/git/blob", params={"ref": "dev", "path": "hello.py"})
    assert b.status_code == 200 and "adopted" in b.json()["content"]
    # une URL invalide → 400 (clone échoué remonté en ValueError), pas un 500
    bad = c.post("/api/projects", json={"slug": "bad", "source_url": str(tmp_path / "nope")})
    assert bad.status_code == 400


def test_docs_endpoint_reads_tool_card_then_readme_then_none(client, tmp_path):
    """L'onglet Docs lit la carte `docs/tool-card.md` depuis le repo (SoT bare) ; repli `README.md` ;
    `found:false` si aucune des deux. Read-only, bare-safe (aucun working-tree)."""
    c, _ = client
    genv = {"PATH": os.environ.get("PATH", ""), "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}

    def _seed(name: str, files: dict[str, str]) -> str:
        up = tmp_path / name
        up.mkdir()
        run.run(["git", "init", "-q", "-b", "dev", str(up)], env=genv)
        for rel, content in files.items():
            p = up / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        run.run(["git", "-C", str(up), "add", "-A"], env=genv)
        run.run(["git", "-C", str(up), "commit", "-q", "-m", "seed"], env=genv)
        return str(up)

    # (1) repo AVEC la carte → la carte gagne (même si un README existe aussi)
    src = _seed("carded", {"docs/tool-card.md": "# Mon outil\n\nPourquoi avec Claude.\n",
                           "README.md": "# readme dev\n"})
    c.post("/api/projects", json={"slug": "carded", "kind": "tool", "source_url": src})
    d = c.get("/api/projects/carded/docs").json()
    assert d["found"] is True and d["path"] == "docs/tool-card.md" and "Mon outil" in d["content"]

    # (2) repo SANS carte mais AVEC README → repli README
    src2 = _seed("readmeonly", {"README.md": "# juste un readme\n"})
    c.post("/api/projects", json={"slug": "readmeonly", "kind": "tool", "source_url": src2})
    d2 = c.get("/api/projects/readmeonly/docs").json()
    assert d2["found"] is True and d2["path"] == "README.md" and "juste un readme" in d2["content"]

    # (3) repo SANS carte NI README → found:false (l'UI affiche un EmptyState, pas une erreur)
    src3 = _seed("nodocs", {"code.py": "x = 1\n"})
    c.post("/api/projects", json={"slug": "nodocs", "kind": "tool", "source_url": src3})
    d3 = c.get("/api/projects/nodocs/docs").json()
    assert d3["found"] is False and d3["path"] is None

    # projet inconnu → 404 (handler global), jamais un demi-état
    assert c.get("/api/projects/ghost/docs").status_code == 404


def test_bootstrap_route_preview_then_populates_tools_rail(client, tmp_path):
    """GET /api/bootstrap = aperçu idempotent (goto-only) ; POST = adopte les outils du manifeste → ils
    apparaissent classés `kind=tool` (rail « Outils »). Manifeste absent → available:false (no-op)."""
    c, settings = client
    genv = {"PATH": os.environ.get("PATH", ""), "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}
    up = tmp_path / "u-codemap"
    up.mkdir()
    run.run(["git", "init", "-q", "-b", "dev", str(up)], env=genv)
    (up / "index.py").write_text("print('code-map')\n", encoding="utf-8")
    run.run(["git", "-C", str(up), "add", "-A"], env=genv)
    run.run(["git", "-C", str(up), "commit", "-q", "-m", "real"], env=genv)

    assert c.get("/api/bootstrap").json()["available"] is False       # pas de manifeste → wizard générique
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "bootstrap.yaml").write_text(
        json.dumps({"tools": [{"slug": "code-map", "source_url": str(up)}]}), encoding="utf-8")

    pre = c.get("/api/bootstrap").json()
    assert pre["available"] and pre["total"] == 1 and pre["adopted"] == 0   # aperçu : rien encore adopté
    rep = c.post("/api/bootstrap", json={})
    assert rep.status_code == 200 and rep.json()["created"] == ["code-map"]
    # classé kind=tool (rail « Outils ») + VRAI code via l'explorateur
    assert c.get("/api/projects/code-map").json()["kind"] == "tool"
    t = c.get("/api/projects/code-map/git/tree", params={"ref": "dev"})
    assert "index.py" in {e["name"] for e in t.json()["entries"]}
    # idempotence via l'API : 2ᵉ POST → skipped, aperçu adopted:1
    assert c.post("/api/bootstrap", json={}).json()["skipped"] == ["code-map"]
    assert c.get("/api/bootstrap").json()["adopted"] == 1


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
