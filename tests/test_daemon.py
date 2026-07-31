"""Tests du daemon : `build_app` importable **sans god-module** (DI explicite sur app.state), routers de
domaine frappés par TestClient (projects / roadmap / dispatch / gate, y compris un merge e2e sur SoT réel),
et le terminal PTY **local détachable** (`serve_project_terminal` + registre de sessions, plus de ssh)."""
from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cockpit.config import Settings
from cockpit.core import run
from cockpit.daemon import app as app_mod
from cockpit.db import alerts, merge_outcomes, store
from cockpit.dispatch import jobs, worker, worktree
from cockpit.gate import toolchain
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import InternalGit, writeback_env
from cockpit.projects import registry
from cockpit.roadmap import model
from cockpit.terminal import pty
from cockpit.terminal import registry as term_reg


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return TestClient(app_mod.build_app(settings)), settings


def _tok(c) -> list[str]:
    """Sous-protocole du token WS par-instance (garde CSWSH, cf. `daemon.wsguard`). Tout handshake WS le
    passe en `subprotocols=` — sans lui, la garde ferme `1008` avant `accept()`. Aucun `Origin` n'est posé
    par TestClient → la branche « Origin absent » (client non-navigateur) tolère, seul le token gate."""
    return [f"cockpit.token.{c.app.state.ws_token}"]


# -- DI + santé ------------------------------------------------------------------------------------

def test_build_app_has_explicit_di_container_and_health(client):
    c, settings = client
    assert c.app.state.deps.settings == settings        # conteneur DI explicite, pas un global
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_ws_token_endpoint_exposes_instance_token_to_same_origin_front(client):
    """Le front same-origin lit le token WS par-instance ici pour l'injecter au handshake (sous-protocole).
    Le token rendu == celui posé sur `app.state` au boot. (Une page tierce ne peut PAS lire ce corps : CORS
    + same-origin policy ; la garde `wsguard` reste la barrière réelle.)"""
    c, _ = client
    r = c.get("/api/ws-token")
    assert r.status_code == 200
    assert r.json()["token"] == c.app.state.ws_token


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


def test_types_endpoint_lists_valid_registry_and_create_echoes_type(client):
    """P3 : GET /api/types = registre des types OFFERTS (filtré par validation, goto-safe) — alimente le
    dropdown de création. browser-game y figure avec ses métadonnées ; et créer un projet typé échoie son
    type dans la réponse (le contrat que l'UI lit pour confirmer)."""
    c, _ = client
    r = c.get("/api/types")
    assert r.status_code == 200
    by_type = {t["type"]: t for t in r.json()["types"]}
    assert "generic" in by_type and "browser-game" in by_type
    bg = by_type["browser-game"]
    assert bg["version"] == "1" and bg["default_facet"] == "backend"
    assert set(bg["facets"]) == {"frontend", "backend", "game-design", "doc"}
    # boucle complète : le type offert crée réellement un projet typé, et l'API échoie le type créé
    created = c.post("/api/projects", json={"slug": "vr", "project_type": "browser-game"})
    assert created.status_code == 201 and created.json()["project_type"] == "browser-game"


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


def test_roadmap_surfaces_resolved_blueprint(client, monkeypatch):
    """Une feature portant un `blueprint:` (ref STAMP, v9) ressort son verdict résolu sur le board : le client
    MCP est monkeypatché (aucun réseau) pour rendre le corps → `resolved:true` + champs fusionnés. La feature
    sans blueprint ressort `blueprint: null` (rétro-compat)."""
    c, _ = client
    # blueprint_resolver(settings) → un resolver factice qui rend le corps du blueprint (hit MCP simulé)
    body = {"title": "Le gate déterministe", "status": "current"}
    monkeypatch.setattr("cockpit.daemon.routes.roadmap.blueprint_resolver",
                        lambda _s: (lambda _bp: body))
    c.post("/api/projects", json={"slug": "proj"})
    assert c.post("/api/projects/proj/features",
                  json={"slug": "gate", "blueprint": "deterministic-tooling-gate"}).status_code == 201
    c.post("/api/projects/proj/features", json={"slug": "plain"})       # sans blueprint
    rm = c.get("/api/projects/proj/roadmap").json()
    gate = next(f for f in rm["features"] if f["slug"] == "gate")
    plain = next(f for f in rm["features"] if f["slug"] == "plain")
    assert gate["blueprint"]["id"] == "deterministic-tooling-gate"
    assert gate["blueprint"]["resolved"] is True
    assert gate["blueprint"]["title"] == "Le gate déterministe"         # champ fusionné du corps résolu
    assert plain["blueprint"] is None                                    # feature sans blueprint → null


def test_roadmap_blueprint_honest_when_mcp_down(client, monkeypatch):
    """MCP injoignable / non câblé → le board dégrade honnêtement : `resolved:false` + raison, jamais inventé.
    Le contrat de la feature (state/blockers/next) reste inchangé."""
    c, _ = client
    monkeypatch.setattr("cockpit.daemon.routes.roadmap.blueprint_resolver",
                        lambda _s: (lambda _bp: None))                   # resolver rend None (down/empty)
    c.post("/api/projects", json={"slug": "proj"})
    c.post("/api/projects/proj/features", json={"slug": "gate", "blueprint": "deterministic-tooling-gate"})
    rm = c.get("/api/projects/proj/roadmap").json()
    gate = next(f for f in rm["features"] if f["slug"] == "gate")
    assert gate["blueprint"]["id"] == "deterministic-tooling-gate"
    assert gate["blueprint"]["resolved"] is False
    assert gate["blueprint"]["reason"]                                   # raison honnête, jamais inventée


def test_roadmap_check_endpoint_exposes_completeness_gate(client):
    """`GET .../roadmap/check` = MÊME autorité que le CLI, en HTTP. Projet neuf (socle semé) → ok ; une dep
    inter-feature pendante → ok:false + DANGLING_FEATURE_DEP ; la dep feature round-trip sur le board."""
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})                       # generic → socle semé (opérationnel)
    healthy = c.get("/api/projects/proj/roadmap/check").json()
    assert healthy["ok"] is True and healthy["issues"] == []
    # feature avec une dep inter-feature vers une feature INEXISTANTE (facet doc = vocab du bundle generic)
    c.post("/api/projects/proj/features", json={"slug": "code", "facet": "doc", "depends_on": ["ghost"]})
    c.post("/api/features/proj/code/tasks", json={"slug": "impl", "acceptance": "x"})
    bad = c.get("/api/projects/proj/roadmap/check").json()
    assert bad["ok"] is False
    assert "DANGLING_FEATURE_DEP" in {i["kind"] for i in bad["issues"]}
    code = next(f for f in c.get("/api/projects/proj/roadmap").json()["features"] if f["slug"] == "code")
    assert code["depends_on"] == ["ghost"]                               # dep feature round-trip sur le board


def test_roadmap_check_unknown_project_is_404(client):
    c, _ = client
    assert c.get("/api/projects/ghost/roadmap/check").status_code == 404


# -- dispatch (gate no-task-no-dispatch, sans spawn) -----------------------------------------------

def test_dispatch_refused_without_task_no_spawn(client, monkeypatch):
    c, _ = client
    # auth Claude présente (déterministe, indépendant du host CI) → on teste le gate no-task, pas l'auth
    monkeypatch.setattr("cockpit.auth.claude_auth_status",
                        lambda *a, **k: {"authenticated": True, "source": "test"})
    c.post("/api/projects", json={"slug": "proj"})
    c.post("/api/projects/proj/features", json={"slug": "empty"})
    r = c.post("/api/dispatch/proj/empty").json()        # feature sans task → rien à drainer, aucun spawn
    # nouvelle forme (rapport agrégé `run_feature`, identique au CLI) : rien dispatché, feature drainée à vide
    assert r["dispatched"] == 0 and r["runs"] == [] and r["drained"] is True
    assert c.get("/api/jobs/inexistant").status_code == 404


def test_web_dispatch_drains_and_produces_review(client, monkeypatch, fake_tools):
    """Non-régression du dead-end « attend review » : un `POST /api/dispatch` (chemin WEB) DRAINE la feature
    puis la FINALISE (Tier-0 + reviewer) → `GET /api/gate` porte `review.present` (était `False` = le bug),
    gate vert, GO activable. Fakes injectés à la place des runners `claude -p` par défaut (zéro spawn)."""
    c, settings = client
    fake_tools(settings)                                    # hôte provisionné → preflight de dispatch passe
    fake_home = settings.home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))              # trust_workspace n'écrit pas le vrai home
    monkeypatch.setattr("cockpit.auth.claude_auth_status",
                        lambda *a, **k: {"authenticated": True, "source": "test"})

    def _worker(argv, *, cwd, input_text, timeout, env=None):
        p = Path(cwd) / "src" / "note.sh"                   # code-bearing mais Tier-0 N/A → reviewer exigé
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": "fait", "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    def _reviewer(argv, *, cwd, input_text, timeout, env=None):
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": '{"findings":[]}', "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    # Worker ET reviewer passent désormais par `worker._make_default_runner` (le reviewer réutilise le
    # primitive streaming). On route par l'allowlist : le worker porte `WebSearch`, le reviewer non.
    def _route(argv, *, cwd, input_text, timeout, env=None):
        allow = argv[argv.index("--allowedTools") + 1]
        fn = _worker if "WebSearch" in allow else _reviewer
        return fn(argv, cwd=cwd, input_text=input_text, timeout=timeout, env=env)

    monkeypatch.setattr("cockpit.dispatch.worker._make_default_runner", lambda *a, **k: _route)

    conn = store.open_db(settings)                          # seed projet/feature/task (todo) en direct
    registry.create_project(conn, settings, slug="proj")
    # Un projet `generic` n'a ni pyproject ni package.json : depuis le renversement 2026-07-31, la source
    # produite hors routes connues (`src/note.sh`) exige une toolchain DÉCLARÉE — sinon Tier-0 rouge (testé
    # pour lui-même dans test_gate.py). Ce test porte sur le chemin reviewer, il déclare donc comme un vrai
    # projet le ferait.
    InternalGit().overlay_commit(
        registry.sot_path_for(settings, "proj"), branch="dev",
        files={".cockpit/bundle.toml": '[bundle]\nversion = "1"\nproject_type = "generic"\n\n'
                                       '[bundle.gate]\nsteps = [{ name = "declared", argv = ["true"] }]\n'},
        message="chore: déclare la toolchain du projet", identity=("test", "test@local"))
    conn.execute("DELETE FROM tasks")                       # board CONTRÔLÉ : retire le socle d'amorçage
    conn.execute("DELETE FROM features")                    # (sinon le gate socle tiendrait `feat`)
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug="impl")
    conn.commit()
    conn.close()

    r = c.post("/api/dispatch/proj/feat").json()            # draine + finalise (rapport agrégé)
    assert r["merge_ready"] == ["feat"]                     # feature finalisée, gate vert
    st = c.get("/api/gate/proj/feat").json()
    assert st["review"]["present"] is True and st["review"]["fresh"] is True   # LE bug : était False
    assert st["decision"] is not None and st["decision"]["gate_green"] is True


def test_gate_read_surface_exposes_toolchain_verdicts_and_history(client):
    """F4 : la trace se LIT dans l'UI. `GET /api/gate` porte `toolchain` (miroir review/verify) ; `verdicts`
    est la vue de lecture (review + toolchain, findings au niveau route) ; `.../history` rend l'historique
    par SHA → un rouge à SHA-A et un vert à SHA-B coexistent, sans ouvrir la DB à la main."""
    from cockpit.gate import history
    c, settings = client
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    conn.commit()
    history.record_verdict(conn, "proj", "feat", "review", {"reviewed_sha": "A", "counts": {"red": 1}})
    history.record_verdict(conn, "proj", "feat", "review", {"reviewed_sha": "B", "counts": {"red": 0}})
    conn.close()

    st = c.get("/api/gate/proj/feat").json()
    assert "toolchain" in st                                # Tier-0 natif surfacé (miroir review/verify)
    detail = c.get("/api/gate/proj/feat/verdicts").json()
    assert "review" in detail and "toolchain" in detail    # vue de lecture (findings au niveau route)
    hist = c.get("/api/gate/proj/feat/history").json()
    shas = [v["sha"] for v in hist["verdicts"]]
    assert "A" in shas and "B" in shas                     # les DEUX passages sont retrouvables


def test_toolchain_gate_route_injects_tools_env(client, monkeypatch, fake_tools):
    """La route `POST …/toolchain` passe `env=tools_env(settings)` à `run_toolchain`, ALIGNÉE sur la CLI
    (`gate/toolchain.py`) et l'orchestrator. Sans lui, sur un hôte au PATH minimal, la route rendrait un
    verdict DIFFÉRENT de la CLI (faux-rouge d'un veto Tier-0 natif NON-overridable, sans échappatoire).
    Régression : l'ancien appel `run_toolchain(wt, diff_files)` laissait `env=None`."""
    from cockpit import tools
    c, settings = client
    fake_tools(settings)                                    # hôte provisionné (preflight worker + tools_bin)
    fake_home = settings.home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))

    def _py_worker(argv, *, cwd, input_text, timeout, env=None):
        (Path(cwd) / "feature.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": "fait", "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    conn = store.open_db(settings)          # worktree + branche + diff réels (worker écrit un .py)
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug="impl")
    conn.commit()
    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_py_worker)
    conn.execute("UPDATE tasks SET status='done' WHERE slug='impl'")
    conn.commit()
    conn.close()

    captured: dict = {}

    def _spy_run_toolchain(wt, diff_files, *, timeout_s=toolchain.DEFAULT_TIMEOUT_S, env=None):
        captured["env"] = env
        return []

    monkeypatch.setattr(toolchain, "run_toolchain", _spy_run_toolchain)

    r = c.post("/api/gate/proj/feat/toolchain")
    assert r.status_code == 201
    # La route passe EXACTEMENT ce que la CLI et l'orchestrator passent (PATH préfixé de tools_bin) →
    # verdict identique sur un PATH appauvri. L'ancien `env=None` (le bug) divergeait.
    assert captured["env"] is not None
    assert captured["env"] == tools.tools_env(settings)


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


def test_abort_run_endpoint_kills_and_requeues(client):
    """`POST /api/dispatch/{project}/abort` (bouton « Arrêter le run ») marque le job `killed` + intention
    tracée, re-runnable. La route littérale `abort` est déclarée AVANT `/{project}/{feature}` → elle n'est
    PAS avalée avec feature=='abort' (sinon 404). Job sans pid (NULL) → aucun killpg réel (chemin kill
    couvert par test_abort avec killer injecté) : ici on prouve le routage + le teardown DB."""
    c, settings = client
    job_id = _seed_job(settings, "/tmp/none.jsonl")          # job running, pid NULL
    r = c.post("/api/dispatch/proj/abort")
    assert r.status_code == 200                               # route atteinte (pas swallow par /{feature})
    body = r.json()
    assert body["project"] == "proj" and body["aborted"] == 1
    conn = store.open_db(settings)
    try:
        row = jobs.get_job(conn, job_id)
        assert row["status"] == "killed" and row["error"] == "aborted by human"
    finally:
        conn.close()


def test_reconcile_socle_endpoint_reports_result(client):
    """`POST /api/dispatch/{project}/reconcile-socle` (action « Valider l'interview & clôturer le socle ») :
    socle travaillé (feature de travail authorée) → clôt le socle et RAPPORTE en clair (status reconciled,
    N tasks closes, prochaine étape). La route littérale est déclarée AVANT `/{project}/{feature}` → non
    avalée avec feature=='reconcile-socle' (sinon 404/spawn)."""
    c, settings = client
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="proj")
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")
    conn.commit()
    model.add_feature(conn, project_slug="proj", slug="socle", facet="doc")
    model.add_task(conn, feature_ref="proj/socle", slug="cadrage", acceptance="Intention.",
                   mode="interactive")
    model.add_feature(conn, project_slug="proj", slug="build", facet="code")   # feature de travail authorée
    model.add_task(conn, feature_ref="proj/build", slug="impl",   # couvre les axes `doc` (PR B2)
                   acceptance="Structure posée, couverture de tests, exemple d'usage, doc de maintenance.")
    conn.close()

    r = c.post("/api/dispatch/proj/reconcile-socle")
    assert r.status_code == 200                               # route atteinte (pas swallow par /{feature})
    body = r.json()
    assert body["status"] == "reconciled" and body["completed"] is True
    assert body["socle_tasks_closed"] == 1 and "cockpit run" in body["next_step"]
    conn = store.open_db(settings)
    try:
        assert conn.execute("SELECT status FROM tasks WHERE slug='cadrage'").fetchone()["status"] == "done"
    finally:
        conn.close()


def test_lifespan_reconciles_orphan_running_jobs_at_boot(tmp_path):
    """Au démarrage du daemon (lifespan), tout job resté `running` (worker tué / daemon redémarré en plein
    run — dispatch synchrone in-process, aucun thread ne survit) est réconcilié : `killed` + sa task
    `in_progress`→`todo`, la feature redevient dispatchable. Sans quoi le job reste zombie indéfiniment."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    job_id = _seed_job(settings, "/tmp/none.jsonl")          # job `running` + projet/feature/task
    conn = store.open_db(settings)
    conn.execute("UPDATE tasks SET status='in_progress' WHERE slug='schema'")   # task figée (worker mort)
    conn.commit()
    conn.close()
    with TestClient(app_mod.build_app(settings)):            # __enter__ déclenche le lifespan → reconcile
        pass
    conn = store.open_db(settings)
    try:
        assert jobs.get_job(conn, job_id)["status"] == "killed"
        assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "todo"
    finally:
        conn.close()


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

    with c.websocket_connect(f"/ws/dispatch/{job_id}", subprotocols=_tok(c)) as ws:
        e1 = ws.receive_json()
        assert e1["type"] == "assistant" and "démarre" in e1["text"]
        assert e1["tools"][0] == {"name": "Read", "input_summary": "core.py"}
        e2 = ws.receive_json()
        assert e2["type"] == "tool_result" and e2["results"][0]["ok"] is True
        e3 = ws.receive_json()                              # frame terminale de fin de job
        assert e3["type"] == "job" and e3["status"] == "done" and e3["num_turns"] == 3

    # job inconnu (token valide → passe la garde, teste bien le gate d'existence) → refus 1008, jamais un flux
    with pytest.raises(WebSocketDisconnect), \
            c.websocket_connect("/ws/dispatch/inexistant", subprotocols=_tok(c)) as ws:
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
    c, settings = client
    c.post("/api/projects", json={"slug": "proj"})   # SoT neuf : dev + main sur le commit racine seedé
    v = c.get("/api/projects/proj/git")
    assert v.status_code == 200
    body = v.json()
    assert {b["name"] for b in body["branches"]} == {"dev", "main"}
    assert all(b["sha"] and b["subject"] == "root: cockpit seed" for b in body["branches"])
    assert body["tags"] == []                        # SoT neuf : aucun tag
    # dev == main sur un SoT neuf → 0 ahead / 0 behind (aucun merge encore)
    assert body["ahead_behind"] == {"base": "main", "head": "dev", "ahead": 0, "behind": 0}
    assert body["logs"]["dev"][0]["subject"] == "root: cockpit seed"

    # tag posé sur le SoT bare → remonté par la vue git (câblage route bout-en-bout)
    sot = registry.sot_path_for(settings, "proj")
    assert run.run(["git", "-C", str(sot), "tag", "-a", "v1.0", "-m", "release 1.0", "dev"]).ok
    tags = c.get("/api/projects/proj/git").json()["tags"]
    assert [t["name"] for t in tags] == ["v1.0"]
    assert tags[0]["sha"] and tags[0]["subject"] == "release 1.0"
    # projet inconnu → 404 (handler KeyError global), jamais un demi-état inventé
    assert c.get("/api/projects/ghost/git").status_code == 404


def test_git_paths_lists_files_over_http(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})   # SoT neuf auto-seedé (fichiers du scaffold)
    r = c.get("/api/projects/proj/git/paths", params={"ref": "dev"})
    assert r.status_code == 200
    body = r.json()
    assert body["project"] == "proj" and body["ref"] == "dev"
    assert body["paths"] and all(isinstance(p, str) for p in body["paths"])   # liste plate non vide
    assert body["truncated"] is False
    # réf inconnue → 404 ; projet inconnu → 404 (jamais un demi-état inventé)
    assert c.get("/api/projects/proj/git/paths", params={"ref": "nope"}).status_code == 404
    assert c.get("/api/projects/ghost/git/paths", params={"ref": "dev"}).status_code == 404


def test_git_blame_over_http(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})   # SoT neuf auto-seedé (1 commit racine)
    r = c.get("/api/projects/proj/git/blame", params={"ref": "dev", "path": "CLAUDE.md"})
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert lines and all(x["sha"] and x["author"] and x["date"] for x in lines)   # une entrée par ligne
    # réf/chemin invalide → 404 ; dossier → 404 (non-blob) ; projet inconnu → 404
    blame = "/api/projects/{}/git/blame"
    assert c.get(blame.format("proj"), params={"ref": "nope", "path": "CLAUDE.md"}).status_code == 404
    assert c.get(blame.format("proj"), params={"ref": "dev", "path": "docs"}).status_code == 404
    assert c.get(blame.format("ghost"), params={"ref": "dev", "path": "CLAUDE.md"}).status_code == 404


def test_git_search_over_http(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})   # SoT auto-seedé (CLAUDE.md scaffold porte « cockpit »)
    search = "/api/projects/{}/git/search"
    r = c.get(search.format("proj"), params={"ref": "dev", "q": "cockpit"})
    assert r.status_code == 200
    body = r.json()
    assert body["project"] == "proj" and body["ref"] == "dev" and body["q"] == "cockpit"
    assert body["results"] and all({"path", "line", "text"} <= m.keys() for m in body["results"])
    assert body["truncated"] is False and body["count"] == len(body["results"])
    # q vide → 200 vide (pas de match-tout) ; réf inconnue → 404 ; projet inconnu → 404
    empty = c.get(search.format("proj"), params={"ref": "dev", "q": "   "})
    assert empty.status_code == 200 and empty.json()["results"] == []
    assert c.get(search.format("proj"), params={"ref": "nope", "q": "cockpit"}).status_code == 404
    assert c.get(search.format("ghost"), params={"ref": "dev", "q": "cockpit"}).status_code == 404


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


def test_git_sync_reconcile_ff_and_blocks_diverged(client, tmp_path: Path):
    """`POST .../git/sync/reconcile` réconcilie **ff-only** : miroir en avance sur `dev` → ff le SoT (le
    badge retombe `synced`) ; vraie divergence non-ff → **bloqué**, aucune mutation. Preview via le GET
    idempotent, exécution via ce POST (jamais un dry-run POST). Projet absent → 404."""
    c, settings = client
    env = writeback_env(("T", "t@example.invalid"))

    def advance(repo: Path, branch: str, wt: Path) -> str:
        assert run.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt), branch], env=env).ok
        (wt / f"{wt.name}.txt").write_text("x\n", encoding="utf-8")
        assert run.run(["git", "-C", str(wt), "add", "-A"], env=env).ok
        assert run.run(["git", "-C", str(wt), "commit", "-q", "-m", f"adv {branch}"], env=env).ok
        sha = run.run(["git", "-C", str(repo), "rev-parse", branch], env=env).stdout.strip()
        run.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], env=env)  # libère la ref
        return sha

    c.post("/api/projects", json={"slug": "proj"})
    sot = registry.sot_path_for(settings, "proj")
    mirror = tmp_path / "mirror.git"
    assert run.run(["git", "clone", "--bare", "-q", str(sot), str(mirror)]).ok
    assert c.patch("/api/projects/proj", json={"mirror_remote": str(mirror)}).status_code == 200

    # miroir en avance sur `dev` → reconcile ff le SoT ; badge retombe `synced`
    mir_dev = advance(mirror, "dev", tmp_path / "m1")
    rep = c.post("/api/projects/proj/git/sync/reconcile").json()
    assert rep["actions"]["dev"]["action"] == "fast_forward" and rep["actions"]["dev"]["to"] == mir_dev
    assert rep["changed"] is True and rep["blocked"] == []
    assert c.get("/api/projects/proj/git/sync").json()["state"] == "synced"   # rattrapé

    # vraie divergence non-ff (avancé des deux côtés) → BLOQUÉ, aucune mutation
    advance(mirror, "dev", tmp_path / "m2")
    local_dev = advance(sot, "dev", tmp_path / "s2")
    blocked = c.post("/api/projects/proj/git/sync/reconcile").json()
    assert blocked["actions"]["dev"]["action"] == "blocked_diverged" and blocked["blocked"] == ["dev"]
    assert blocked["changed"] is False
    assert run.run(["git", "-C", str(sot), "rev-parse", "dev"]).stdout.strip() == local_dev  # intact

    assert c.post("/api/projects/ghost/git/sync/reconcile").status_code == 404


def test_tool_sync_ff_and_fail_close_on_project(client, tmp_path: Path, monkeypatch):
    """`POST .../tool/sync` re-synchronise un outil **pull-only ff** : amont en avance sur `dev` → ff le SoT
    (auto-répare le refspec du clone bare au passage), `changed=True`. **Fail-close** : un projet refuse la
    route → 409 (sa voie est `reconcile`) ; entité absente → 404. Pré-chauffage d'index monkeypatché (test
    — la glue best-effort est couverte par test_toolsync)."""
    c, settings = client
    from cockpit import toolsync
    from cockpit.codemap.index import IndexHandle
    monkeypatch.setattr(toolsync, "ensure_index",
                        lambda s, p, sot, *, ref="dev", runner=None:
                        IndexHandle(project=p, ref=ref, sha="d", root=Path(sot)))
    env = writeback_env(("T", "t@example.invalid"))

    # upstream « GitHub » non-bare (main+dev) puis adoption comme OUTIL (clone bare, origin sans refspec)
    up = tmp_path / "upstream"
    up.mkdir()
    assert run.run(["git", "init", "-q", "-b", "main", str(up)], env=env).ok
    (up / "README.md").write_text("outil\n", encoding="utf-8")
    assert run.run(["git", "-C", str(up), "add", "-A"], env=env).ok
    assert run.run(["git", "-C", str(up), "commit", "-q", "-m", "adoption"], env=env).ok
    assert run.run(["git", "-C", str(up), "branch", "dev"], env=env).ok
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="code-map", kind="tool", source_url=str(up))
    conn.close()
    sot = registry.sot_path_for(settings, "code-map")

    # à jour d'abord → 200, already_synced, changed=False
    r0 = c.post("/api/projects/code-map/tool/sync")
    assert r0.status_code == 200 and r0.json()["actions"]["dev"] == {"action": "already_synced"}
    assert r0.json()["changed"] is False

    # l'amont avance sur dev → ff descendant, SoT à origin/dev
    assert run.run(["git", "-C", str(up), "checkout", "-q", "dev"], env=env).ok
    (up / "work.txt").write_text("amont\n", encoding="utf-8")
    assert run.run(["git", "-C", str(up), "add", "-A"], env=env).ok
    assert run.run(["git", "-C", str(up), "commit", "-q", "-m", "advance dev"], env=env).ok
    up_dev = run.run(["git", "-C", str(up), "rev-parse", "dev"], env=env).stdout.strip()
    r1 = c.post("/api/projects/code-map/tool/sync").json()
    assert r1["actions"]["dev"]["action"] == "fast_forward" and r1["actions"]["dev"]["to"] == up_dev
    assert r1["changed"] is True and r1["index_refreshed"] is True
    assert run.run(["git", "-C", str(sot), "rev-parse", "dev"], env=env).stdout.strip() == up_dev

    # fail-close : un PROJET refuse la route pull-only → 409 (sa voie de sync est reconcile)
    c.post("/api/projects", json={"slug": "my-proj"})
    assert c.post("/api/projects/my-proj/tool/sync").status_code == 409
    # entité absente → 404
    assert c.post("/api/projects/ghost/tool/sync").status_code == 404


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
    # enrichissement Phase B : dernier commit par entrée + « latest commit » du dossier (SoT neuf = 1 commit)
    assert entries["CLAUDE.md"]["last_commit"]["subject"] == "root: cockpit seed"
    assert entries[".claude"]["last_commit"]["short"] and entries[".claude"]["last_commit"]["date"]
    latest = body["latest_commit"]
    assert latest["subject"] == "root: cockpit seed" and latest["author"] == "cockpit"
    assert latest["count"] == 1
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


def test_git_raw_and_download_serve_bytes_with_safe_headers(client, monkeypatch):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})   # SoT neuf : CLAUDE.md est un fichier texte du payload
    # raw : type coercé text/plain (anti-XSS same-origin) + nosniff + inline ; octets réels servis
    raw = c.get("/api/projects/proj/git/raw", params={"ref": "dev", "path": "CLAUDE.md"})
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "text/plain; charset=utf-8"
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert raw.headers["content-disposition"] == 'inline; filename="CLAUDE.md"'
    # octets réels servis (round-trip vs le contenu texte de git/blob, qui décode le même blob)
    blob = c.get("/api/projects/proj/git/blob", params={"ref": "dev", "path": "CLAUDE.md"}).json()
    assert raw.content and raw.content.decode() == blob["content"]
    # download : octet-stream + attachment (jamais rendu inline), mêmes octets
    dl = c.get("/api/projects/proj/git/download", params={"ref": "dev", "path": "CLAUDE.md"})
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/octet-stream"
    assert dl.headers["content-disposition"] == 'attachment; filename="CLAUDE.md"'
    assert dl.headers["x-content-type-options"] == "nosniff" and dl.content == raw.content
    # 404 : chemin introuvable, dossier (non-blob), projet absent
    assert c.get("/api/projects/proj/git/raw",
                 params={"ref": "dev", "path": "nope.txt"}).status_code == 404
    assert c.get("/api/projects/proj/git/download",
                 params={"ref": "dev", "path": ".claude"}).status_code == 404
    assert c.get("/api/projects/ghost/git/raw",
                 params={"ref": "dev", "path": "CLAUDE.md"}).status_code == 404
    # 413 SIGNALÉ au-delà du plafond (jamais un flux tronqué en silence)
    from cockpit.git import internal
    monkeypatch.setattr(internal, "_MAX_BLOB_READ", 4)
    assert c.get("/api/projects/proj/git/raw",
                 params={"ref": "dev", "path": "CLAUDE.md"}).status_code == 413


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


def test_api_version_reports_build_provenance(client):
    c, _ = client
    r = c.get("/api/version")
    assert r.status_code == 200
    v = r.json()
    assert "version" in v and "sha" in v and "comparable" in v                 # signal de fraîcheur présent
    assert v["comparable"] is False                            # pas de miroir cockpit local en test → honnête

    # le bloc `build` doit AUSSI apparaître (additif) dans /api/onboarding, sans casser le reste
    st = c.get("/api/onboarding").json()
    assert st["build"]["comparable"] is False and st["complete"] in (True, False)


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


class _ScriptWS:
    """WebSocket scriptable pour piloter `serve_project_terminal` sans réseau : collecte les octets + les
    frames texte, et ne rend `websocket.disconnect` que sur ordre du test (`disconnect()`) — sinon `receive`
    bloque (comme un vrai client resté connecté), laissant l'EOF du PTY décider de la fin."""

    def __init__(self) -> None:
        self.sent = bytearray()
        self.texts: list[str] = []
        self.closed = False
        self._disconnect = asyncio.Event()

    async def send_bytes(self, b: bytes) -> None:
        self.sent.extend(b)

    async def send_text(self, t: str) -> None:
        self.texts.append(t)

    async def receive(self) -> dict:
        await self._disconnect.wait()
        return {"type": "websocket.disconnect"}

    def disconnect(self) -> None:
        self._disconnect.set()

    async def close(self) -> None:
        self.closed = True


async def _until(pred, *, timeout: float = 5.0) -> None:
    """Attend qu'un prédicat devienne vrai (poll court) — pas de `time` dans la boucle (l'horloge peut être
    injectée ailleurs)."""
    for _ in range(int(timeout / 0.02)):
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition non atteinte dans le délai")


def test_terminal_detaches_on_disconnect_and_shell_survives(tmp_path):
    """La feature : à la déconnexion WS, le shell SURVIT (détaché au registre), il n'est PAS tué. La 1ʳᵉ
    connexion s'annonce `fresh:true` (le client n'en fait qu'une bannière « session neuve »)."""
    async def scenario():
        reg = term_reg.PtySessionRegistry()
        ws = _ScriptWS()
        argv = ["/bin/sh", "-c", "sleep 30"]
        task = asyncio.create_task(pty.serve_project_terminal(
            ws, reg, session_key="p", argv=argv, cwd=str(tmp_path), env=None))
        await _until(lambda: bool(ws.texts))                 # la frame de session est partie
        ws.disconnect()
        await task
        sess = reg.get("p")
        assert sess is not None and sess.alive() and sess.detached_at is not None
        assert sess.proc.poll() is None                      # shell TOUJOURS vivant (détaché, pas tué)
        assert json.loads(ws.texts[0]) == {"t": "session", "fresh": True}
        reg.close_all()                                      # cleanup : tue le sleep
        assert sess.proc.wait(timeout=5) is not None
    asyncio.run(scenario())


def test_terminal_reattach_replays_scrollback(tmp_path):
    """Reconnexion → ré-attache la même session : le scrollback est REJOUÉ dans le xterm recréé, et la frame
    s'annonce `fresh:false` (bannière « session restaurée » — la ré-attache REPREND le process en cours)."""
    async def scenario():
        reg = term_reg.PtySessionRegistry()
        argv = ["/bin/sh", "-c", "printf MARKER123; sleep 30"]
        ws1 = _ScriptWS()
        t1 = asyncio.create_task(pty.serve_project_terminal(
            ws1, reg, session_key="p", argv=argv, cwd=str(tmp_path), env=None))
        await _until(lambda: b"MARKER123" in ws1.sent)
        ws1.disconnect()
        await t1
        assert reg.get("p").detached_at is not None          # détachée, pas tuée
        ws2 = _ScriptWS()
        t2 = asyncio.create_task(pty.serve_project_terminal(
            ws2, reg, session_key="p", argv=argv, cwd=str(tmp_path), env=None))
        await _until(lambda: b"MARKER123" in ws2.sent)       # scrollback REJOUÉ sur la ré-attache
        assert json.loads(ws2.texts[0])["fresh"] is False    # ré-attache → pas de re-run
        ws2.disconnect()
        await t2
        reg.close_all()
    asyncio.run(scenario())


def test_terminal_eof_tears_down_and_deregisters(tmp_path):
    """Le shell sort de lui-même (EOF) → relais de la sortie, fermeture du socket, retrait du registre (pas
    de session zombie)."""
    async def scenario():
        reg = term_reg.PtySessionRegistry()
        ws = _ScriptWS()                                     # ne déconnecte jamais → seul l'EOF sort
        await pty.serve_project_terminal(
            ws, reg, session_key="p", argv=["/bin/sh", "-c", "printf DONE"], cwd=str(tmp_path), env=None)
        assert b"DONE" in ws.sent and ws.closed is True
        assert reg.get("p") is None                          # session morte retirée du registre
    asyncio.run(scenario())


def test_reaper_terminates_detached_session_past_ttl(tmp_path):
    """Le reaper (clock + killer INJECTÉS) tue une session détachée au-delà du TTL — sans dormir ni tuer un
    vrai process à l'aveugle."""
    async def scenario():
        now = [0.0]
        calls: list[int] = []

        def killer(pgid: int, sig: int) -> None:
            calls.append(sig)
            os.killpg(pgid, sig)                             # observe ET tue pour de vrai (aucun zombie)

        reg = term_reg.PtySessionRegistry(clock=lambda: now[0], killer=killer)
        ws = _ScriptWS()
        ws.disconnect()                                      # déconnexion immédiate → détach à t=0
        await pty.serve_project_terminal(
            ws, reg, session_key="p", argv=["/bin/sh", "-c", "sleep 30"], cwd=str(tmp_path), env=None)
        sess = reg.get("p")
        assert sess is not None and sess.detached_at == 0.0
        assert reg.reap() == []                              # pas encore expirée
        now[0] = term_reg.DETACH_TTL_S + 1
        assert reg.reap() == ["p"]                            # TTL franchi → reapée
        assert signal.SIGTERM in calls and reg.get("p") is None
        assert sess.proc.wait(timeout=5) is not None          # shell tué
    asyncio.run(scenario())


def test_terminal_ws_refuses_unknown_project_before_accept(client, monkeypatch):
    """Correctif F2 : un projet inexistant est refusé (1008) AVANT `accept()` — plus de `bash -l` spawné
    dans un dir absent qui crashait post-accept. C'est désormais le **seul** gate du WS terminal (le gate
    `claude_auth` a été retiré : le terminal ouvre un shell, il ne spawne pas de worker `claude`). On force
    l'hôte **non authentifié** pour prouver que le refus vient bien du **gate projet**, pas d'un gate d'auth
    (qui n'existe plus ici)."""
    c, _ = client
    monkeypatch.setattr("cockpit.auth.claude_auth_status",
                        lambda *a, **k: {"authenticated": False, "source": None})
    # fermé avant accept → disconnect levé au connect
    with pytest.raises(WebSocketDisconnect), \
            c.websocket_connect("/ws/terminal/does-not-exist", subprotocols=_tok(c)):
        pass


def test_terminal_ws_opens_when_host_unauthenticated(client, monkeypatch):
    """Fix deadlock d'onboarding : hôte NON authentifié + projet **valide** → le WS **s'ouvre** (plus de
    refus). Le terminal est la surface où l'utilisateur lance son `claude login` ; le gater sur l'auth déjà
    présente était un deadlock poule-et-œuf. On prouve que le routeur a passé sa décision (gate projet OK →
    `accept()` → délègue) : on reçoit la frame de contrôle de session `{"t":"session", ...}`. `serve` est
    faké (le vrai est couvert par les tests de détache/ré-attache) pour isoler la décision du routeur."""
    c, _ = client
    c.post("/api/projects", json={"slug": "real"})       # projet existant → gate projet passe
    c.app.state.terminals = term_reg.PtySessionRegistry()  # normalement posé par le lifespan (app.py:96)
    monkeypatch.setattr("cockpit.auth.claude_auth_status",
                        lambda *a, **k: {"authenticated": False, "source": None})

    async def _fake_serve(websocket, registry, **kwargs):
        await websocket.send_text(json.dumps({"t": "session", "fresh": True}))

    monkeypatch.setattr("cockpit.terminal.pty.serve_project_terminal", _fake_serve)
    with c.websocket_connect("/ws/terminal/real", subprotocols=_tok(c)) as ws:   # accept OK
        assert json.loads(ws.receive_text()) == {"t": "session", "fresh": True}


def test_terminal_and_interview_ws_are_distinct_sessions(client, monkeypatch):
    """L'interview est une session PTY DÉDIÉE : /ws/interview/{p} sert avec la clé `interview:{p}` et l'argv
    `cockpit interview`, DISTINCTE de la session shell /ws/terminal/{p} (clé `{p}`, argv `bash -l`). C'est le
    cœur du fix : les deux flavors coexistent sans collision → la session interview a sa propre fraîcheur, le
    shell de login persistant ne la bloque plus (fini le handoff tapé gaté sur `fresh`)."""
    c, _ = client
    c.post("/api/projects", json={"slug": "real"})
    c.app.state.terminals = term_reg.PtySessionRegistry()
    seen: list[tuple[str, list[str]]] = []

    async def _fake_serve(websocket, registry, *, session_key, argv, cwd, env):
        seen.append((session_key, argv))
        await websocket.send_text(json.dumps({"t": "session", "fresh": True}))

    monkeypatch.setattr("cockpit.terminal.pty.serve_project_terminal", _fake_serve)
    with c.websocket_connect("/ws/terminal/real", subprotocols=_tok(c)) as ws:
        assert json.loads(ws.receive_text())["t"] == "session"
    with c.websocket_connect("/ws/interview/real", subprotocols=_tok(c)) as ws:
        assert json.loads(ws.receive_text())["t"] == "session"

    assert seen == [
        ("real", pty.local_shell_argv()),
        ("interview:real", pty.interview_argv("real")),
    ]


def test_terminal_ws_injects_mcp_config_at_project_cwd_when_wired(client, monkeypatch):
    """MCP câblé → ouvrir un terminal projet écrit un `.mcp.json` au cwd du PTY (racine projet) : un `claude`
    lancé dans ce terminal DÉCOUVRE le MCP (`/mcp` le liste, solve-mode). Ferme le trou « /mcp vide en
    terminal interactif » (l'infra MCP ne visait que le worker headless)."""
    c, settings = client
    c.post("/api/projects", json={"slug": "real"})
    c.app.state.terminals = term_reg.PtySessionRegistry()
    monkeypatch.setenv("COCKPIT_MCP_JWT_SECRET_REF", "ref")           # MCP câblé
    monkeypatch.setattr("cockpit.provision.mcp.cred_resolver", lambda s: (lambda r: "k" * 40))

    async def _fake_serve(websocket, registry, **kwargs):
        await websocket.send_text(json.dumps({"t": "session", "fresh": True}))

    monkeypatch.setattr("cockpit.terminal.pty.serve_project_terminal", _fake_serve)
    with c.websocket_connect("/ws/terminal/real", subprotocols=_tok(c)) as ws:
        ws.receive_text()
    mcp_json = settings.projects_root / "real" / ".mcp.json"
    assert mcp_json.is_file()
    cfg = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert cfg["mcpServers"]["vault-catalogs"]["headers"]["Authorization"].startswith("Bearer ")


def test_terminal_ws_mcp_injection_is_honest_noop_when_not_wired(client, monkeypatch):
    """MCP non câblé (install sans corpus privé) → aucun `.mcp.json` écrit : l'injection est un no-op honnête,
    le terminal s'ouvre normalement (jamais de crash sur le câblage MCP)."""
    c, settings = client
    c.post("/api/projects", json={"slug": "real"})
    c.app.state.terminals = term_reg.PtySessionRegistry()
    monkeypatch.delenv("COCKPIT_MCP_JWT_SECRET_REF", raising=False)   # MCP non câblé

    async def _fake_serve(websocket, registry, **kwargs):
        await websocket.send_text(json.dumps({"t": "session", "fresh": True}))

    monkeypatch.setattr("cockpit.terminal.pty.serve_project_terminal", _fake_serve)
    with c.websocket_connect("/ws/terminal/real", subprotocols=_tok(c)) as ws:
        assert json.loads(ws.receive_text())["t"] == "session"       # ouverture normale
    assert not (settings.projects_root / "real" / ".mcp.json").exists()


def test_interview_ws_refuses_unknown_project_before_accept(client, monkeypatch):
    """Même garde projet que le shell : un projet inexistant est refusé (1008) AVANT `accept()` (sinon
    `Popen(cwd=absent)` crashe post-accept). Prouvé sans auth (le WS interview ne gate pas l'auth ; c'est
    `cockpit interview` côté CLI qui la regate dans le PTY)."""
    c, _ = client
    monkeypatch.setattr("cockpit.auth.claude_auth_status",
                        lambda *a, **k: {"authenticated": False, "source": None})
    with pytest.raises(WebSocketDisconnect), \
            c.websocket_connect("/ws/interview/does-not-exist", subprotocols=_tok(c)):
        pass


# -- garde CSWSH : Origin + token par-instance AVANT accept (cf. daemon.wsguard) --------------------

def _open_terminal_ok(c, monkeypatch):
    """Prépare un projet valide + un `serve` faké qui annonce la session — factorise les tests de garde WS
    (on isole la DÉCISION de la garde, le vrai `serve` est couvert par les tests détache/ré-attache)."""
    c.post("/api/projects", json={"slug": "real"})
    c.app.state.terminals = term_reg.PtySessionRegistry()

    async def _fake_serve(websocket, registry, **kwargs):
        await websocket.send_text(json.dumps({"t": "session", "fresh": True}))

    monkeypatch.setattr("cockpit.terminal.pty.serve_project_terminal", _fake_serve)


def test_ws_refuses_handshake_without_token(client, monkeypatch):
    """Un handshake WS SANS le sous-protocole token est fermé (1008) AVANT `accept()`, même sur un projet
    valide. Le token par-instance est la barrière anti-client-non-navigateur + defense-in-depth."""
    c, _ = client
    _open_terminal_ok(c, monkeypatch)
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws/terminal/real"):
        pass


def test_ws_refuses_handshake_with_wrong_token(client, monkeypatch):
    """Un token FAUX est refusé (comparaison temps-constant serveur) — pas seulement un token absent."""
    c, _ = client
    _open_terminal_ok(c, monkeypatch)
    with pytest.raises(WebSocketDisconnect), \
            c.websocket_connect("/ws/terminal/real", subprotocols=["cockpit.token.deadbeef"]):
        pass


def test_ws_refuses_cross_origin_even_with_valid_token(client, monkeypatch):
    """Cœur anti-CSWSH : une page tierce (Origin hors politique) est fermée AVANT `accept()` MÊME avec un
    token valide — le contrôle d'Origin est la barrière anti-navigateur (Origin non-forgeable depuis une
    page). Prouve que la garde ne se réduit pas au token."""
    c, _ = client
    _open_terminal_ok(c, monkeypatch)
    with pytest.raises(WebSocketDisconnect), \
            c.websocket_connect("/ws/terminal/real", subprotocols=_tok(c),
                                headers={"origin": "http://evil.example"}):
        pass


def test_ws_accepts_same_origin_with_valid_token(client, monkeypatch):
    """Le front same-origin (Origin == Host de l'instance) + token valide → accepté, sans configuration.
    TestClient pose `Host: testserver` → un Origin `http://testserver` est same-origin (Origin↔Host)."""
    c, _ = client
    _open_terminal_ok(c, monkeypatch)
    with c.websocket_connect("/ws/terminal/real", subprotocols=_tok(c),
                             headers={"origin": "http://testserver"}) as ws:
        assert json.loads(ws.receive_text())["t"] == "session"


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


# -- alertes (v17, no-silent-block) : lecture + acquittement via HTTP ------------------------------

def test_alerts_api_lists_open_and_acks(client):
    """GET /api/alerts rend les alertes ouvertes + leur compte (badge/centre du header) ; POST
    /api/alerts/{id}/ack les acquitte (open→acked, sortent du compteur) ; un id inconnu → 404."""
    c, settings = client
    conn = store.open_db(settings)
    try:
        alerts.emit_alert(conn, project="proj", feature_ref="proj/feat", feature="feat", kind="gate_red",
                          reason="Tier-1 : aucune revue", tier="tier1", findings=["b1"])
        alerts.emit_alert(conn, project="proj", feature_ref="proj/other", feature="other",
                          kind="worker_failed", reason="boom")
    finally:
        conn.close()

    r = c.get("/api/alerts")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and {a["kind"] for a in body["alerts"]} == {"gate_red", "worker_failed"}
    gate = next(a for a in body["alerts"] if a["kind"] == "gate_red")
    assert gate["feature"] == "feat" and gate["tier"] == "tier1" and gate["findings"] == ["b1"]

    aid = gate["id"]
    ack = c.post(f"/api/alerts/{aid}/ack")
    assert ack.status_code == 200 and ack.json()["status"] == "acked"
    assert c.get("/api/alerts").json()["count"] == 1              # l'acquittée sort du compteur
    assert c.post("/api/alerts/does-not-exist/ack").status_code == 404   # KeyError → 404 (handler global)


def test_reliability_api_reads_and_marks(client):
    """GET /api/reliability/{projet} rend le taux + les merges verts ; POST /{projet}/mark marque l'issue
    aval (→ le taux tombe) ; GET /api/reliability agrège le global ; projet/merge inconnu → 404."""
    c, settings = client
    conn = store.open_db(settings)
    try:
        registry.create_project(conn, settings, slug="proj", name="Proj")
        merge_outcomes.record_merge(conn, project="proj", feature="a", feature_ref="proj/a", sha="sa")
        merge_outcomes.record_merge(conn, project="proj", feature="b", feature_ref="proj/b", sha="sb")
    finally:
        conn.close()

    r = c.get("/api/reliability/proj")
    assert r.status_code == 200
    body = r.json()
    assert body["n_merges_verts"] == 2 and body["n_adverse"] == 0 and body["taux"] == 1.0

    mk = c.post("/api/reliability/proj/mark", json={"feature": "a", "outcome": "reverted", "note": "ko"})
    assert mk.status_code == 200 and mk.json()["outcome"] == "reverted"
    assert c.get("/api/reliability/proj").json()["taux"] == 0.5      # 1 vert tenu sur 2

    g = c.get("/api/reliability").json()
    assert g["scope"] == "global" and g["n_merges_verts"] == 2 and g["n_adverse"] == 1

    assert c.get("/api/reliability/ghost").status_code == 404        # projet inconnu → 404
    assert c.post("/api/reliability/proj/mark",
                  json={"feature": "nope", "outcome": "reverted"}).status_code == 404   # merge inconnu → 404
