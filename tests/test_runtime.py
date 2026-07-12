"""Tests du runtime (P2) : backend compose (argv scopé, parsing ps, garde d'échec), engine (transitions
d'état, pool de ports deploy DISTINCT, isolation namespace, 2 projets sans collision) et routes POST du
lifecycle. Le transport (Runner) et le backend sont **injectés** → aucun vrai conteneur n'est spawné."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cockpit.config import Settings
from cockpit.core.run import RunResult
from cockpit.daemon import app as app_mod
from cockpit.db import store
from cockpit.dispatch import ports
from cockpit.projects import deployments, registry
from cockpit.runtime import backend as backend_mod
from cockpit.runtime import engine
from cockpit.runtime.backend import PodmanCompose
from cockpit.runtime.paths import compose_project_name


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return TestClient(app_mod.build_app(settings)), settings


# -- doubles injectés ------------------------------------------------------------------------------

class FakeGit:
    """GitBackend factice : matérialise un workdir (avec un compose.yaml) sans vrai SoT."""

    def __init__(self, sha: str = "deadbeefcafe") -> None:
        self.sha = sha
        self.archived: list[tuple[str, str]] = []

    def archive(self, sot: Path, ref: str, dest_dir: Path) -> None:
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        (Path(dest_dir) / "compose.yaml").write_text("services: {}\n")
        self.archived.append((ref, str(dest_dir)))

    def feature_sha(self, sot: Path, ref: str) -> str:
        return self.sha


class FakeBackend:
    """ComposeBackend factice : enregistre les appels, ne spawne rien."""

    def __init__(self, *, fail_up: bool = False, ps_rows: list[dict] | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail_up = fail_up
        self.ps_rows = ps_rows or []

    def up(self, name: str, workdir: Path, *, env: dict | None = None) -> None:
        self.calls.append(("up", name, str(workdir), dict(env or {})))
        if self.fail_up:
            raise backend_mod.ComposeError("build a échoué")

    def down(self, name: str, workdir: Path, *, env: dict | None = None) -> None:
        self.calls.append(("down", name, str(workdir), dict(env or {})))

    def restart(self, name: str, workdir: Path, *, env: dict | None = None) -> None:
        self.calls.append(("restart", name, str(workdir), dict(env or {})))

    def ps(self, name: str, workdir: Path, *, env: dict | None = None) -> list[dict]:
        self.calls.append(("ps", name, str(workdir), dict(env or {})))
        return self.ps_rows


# -- paths : le nom de compose-project EST la frontière d'isolation --------------------------------

def test_compose_project_name_is_isolation_namespace():
    assert compose_project_name("alpha", "dev") == "cockpit-alpha-dev"
    assert compose_project_name("alpha", "dev") != compose_project_name("beta", "dev")   # inter-projets
    assert compose_project_name("alpha", "dev") != compose_project_name("alpha", "main")  # inter-branches


# -- backend : argv scopé, réglage du moteur, parsing ps, garde d'échec ----------------------------

def _recorder():
    calls: list[tuple] = []

    def rec(argv, *, cwd, env, timeout):
        calls.append((list(argv), str(cwd), dict(env), timeout))
        return RunResult(argv=list(argv), returncode=0, stdout="", stderr="")

    return calls, rec


def test_podman_compose_builds_scoped_argv_and_merges_env(tmp_path: Path):
    calls, rec = _recorder()
    PodmanCompose(cmd=("podman", "compose"), runner=rec).up(
        "cockpit-x-dev", tmp_path, env={"COCKPIT_PORT": "5250"})
    argv, cwd, env, _ = calls[0]
    assert argv == ["podman", "compose", "-p", "cockpit-x-dev", "up", "-d", "--build"]
    assert cwd == str(tmp_path)
    assert env["COCKPIT_PORT"] == "5250"          # overlay injecté (interpolation ${COCKPIT_PORT})
    assert "PATH" in env                          # os.environ mergé (run REMPLACE l'env)


def test_compose_engine_is_a_setting_not_code(tmp_path: Path):
    calls, rec = _recorder()
    PodmanCompose(cmd=("docker", "compose"), runner=rec).down("cockpit-x-dev", tmp_path)
    assert calls[0][0][:2] == ["docker", "compose"]   # bascule moteur = simple réglage


def test_compose_checked_raises_on_failure(tmp_path: Path):
    def rec_fail(argv, *, cwd, env, timeout):
        return RunResult(argv=list(argv), returncode=1, stdout="", stderr="nope")
    with pytest.raises(backend_mod.ComposeError):
        PodmanCompose(runner=rec_fail).up("n", tmp_path)


def test_parse_ps_handles_array_and_ndjson():
    assert backend_mod._parse_ps('[{"State": "running"}]') == [{"State": "running"}]
    ndjson = '{"State": "running"}\n{"State": "exited"}'
    assert backend_mod._parse_ps(ndjson) == [{"State": "running"}, {"State": "exited"}]
    assert backend_mod._parse_ps("") == []            # vide → [] (jamais un faux-vert)


def test_is_running_tolerates_docker_and_podman_shapes():
    assert backend_mod.is_running({"State": "running"})
    assert backend_mod.is_running({"Status": "Up 3 minutes"})
    assert not backend_mod.is_running({"State": "exited"})


# -- engine : transitions d'état -------------------------------------------------------------------

def _dep(conn, slug, branch="dev"):
    return deployments.get_deployment(conn, registry.get_project(conn, slug)["id"], branch)


def test_deploy_records_running_with_port_url_sha_and_compose_ref(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend()
    dep = engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit("cafe1234"), backend=be)
    assert dep["status"] == "running"
    assert engine.DEPLOY_RANGE[0] <= dep["port"] <= engine.DEPLOY_RANGE[1]
    assert dep["url"] == f"http://127.0.0.1:{dep['port']}"
    assert dep["last_deploy_sha"] == "cafe1234"
    assert dep["compose_ref"] == "cockpit-svc-dev"
    op, name, _, env = be.calls[0]
    assert (op, name) == ("up", "cockpit-svc-dev")           # namespace d'isolation
    assert env["COCKPIT_PORT"] == str(dep["port"])           # port injecté dans l'env compose


def test_deploy_port_is_in_deploy_pool_not_worktree_pool(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    wt = ports.reserve(conn, project="svc", purpose="worktree:feat")    # pool worktree (5170-5249)
    assert ports.DEFAULT_RANGE[0] <= wt["port"] <= ports.DEFAULT_RANGE[1]
    dep = engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=FakeBackend())
    assert engine.DEPLOY_RANGE[0] <= dep["port"] <= engine.DEPLOY_RANGE[1]   # pool deploy DISTINCT
    assert dep["port"] != wt["port"]


def test_two_projects_deploy_without_collision(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="alpha")
    registry.create_project(conn, settings, slug="beta")
    a = engine.deploy(conn, settings, slug="alpha", branch="dev", git=FakeGit(), backend=FakeBackend())
    b = engine.deploy(conn, settings, slug="beta", branch="dev", git=FakeGit(), backend=FakeBackend())
    assert a["compose_ref"] != b["compose_ref"]    # namespaces distincts
    assert a["port"] != b["port"]                  # ports distincts → zéro collision


def test_redeploy_keeps_the_same_port(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    d1 = engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=FakeBackend())
    d2 = engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=FakeBackend())
    assert d1["port"] == d2["port"]                # réservation idempotente (URL stable)


def test_deploy_failure_sets_unhealthy_and_raises(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    with pytest.raises(ValueError, match="deploy échoué"):
        engine.deploy(conn, settings, slug="svc", branch="dev",
                      git=FakeGit(), backend=FakeBackend(fail_up=True))
    assert _dep(conn, "svc")["status"] == "unhealthy"       # jamais un faux-vert


def test_stop_downs_and_marks_stopped_keeping_port(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend()
    up = engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=be)
    st = engine.stop(conn, settings, slug="svc", branch="dev", backend=be)
    assert st["status"] == "stopped"
    assert st["port"] == up["port"]                # port gardé → URL stable, re-up idempotent
    assert (be.calls[-1][0], be.calls[-1][1]) == ("down", "cockpit-svc-dev")


def test_stop_is_a_honest_noop_when_never_deployed(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    dep = engine.stop(conn, settings, slug="svc", branch="dev", backend=FakeBackend())
    assert dep["status"] == "no_deploy"


def test_restart_marks_running(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend()
    engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=be)
    r = engine.restart(conn, settings, slug="svc", branch="dev", backend=be)
    assert r["status"] == "running" and be.calls[-1][0] == "restart"


def test_status_running_when_a_container_is_up(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend(ps_rows=[{"State": "running"}])
    engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=be)
    assert engine.status(conn, settings, slug="svc", branch="dev", backend=be)["status"] == "running"


def test_status_stopped_when_no_container_is_up(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend(ps_rows=[])
    engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=be)
    assert engine.status(conn, settings, slug="svc", branch="dev", backend=be)["status"] == "stopped"


def test_deploy_rejects_invalid_branch_and_unknown_project(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    with pytest.raises(ValueError, match="branche invalide"):
        engine.deploy(conn, settings, slug="svc", branch="prod", git=FakeGit(), backend=FakeBackend())
    with pytest.raises(KeyError):
        engine.deploy(conn, settings, slug="ghost", branch="dev", git=FakeGit(), backend=FakeBackend())


# -- routes POST du lifecycle (backend factice injecté au défaut de l'engine) ----------------------

def test_deploy_route_up_reflected_then_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose", lambda **_kw: FakeBackend())
    assert c.post("/api/projects", json={"slug": "web"}).status_code == 201
    r = c.post("/api/projects/web/deployments/dev/up")
    assert r.status_code == 200
    dep = r.json()["deployment"]
    assert dep["status"] == "running"
    assert dep["port"] is not None and dep["url"].startswith("http://127.0.0.1:")
    live = c.get("/api/projects/web/deployments").json()["deployments"]
    assert next(d for d in live if d["branch"] == "dev")["status"] == "running"   # GET reflète
    down = c.post("/api/projects/web/deployments/dev/down")
    assert down.status_code == 200 and down.json()["deployment"]["status"] == "stopped"


def test_deploy_route_404_unknown_project(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose", lambda **_kw: FakeBackend())
    assert c.post("/api/projects/ghost/deployments/dev/up").status_code == 404


def test_deploy_route_400_invalid_branch(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose", lambda **_kw: FakeBackend())
    c.post("/api/projects", json={"slug": "web"})
    assert c.post("/api/projects/web/deployments/prod/up").status_code == 400
