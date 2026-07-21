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
from cockpit.runtime.paths import compose_project_name, deploy_dir_for


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

    def __init__(self, *, fail_up: bool = False, ps_rows: list[dict] | None = None,
                 log_lines: list[str] | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail_up = fail_up
        # None → un conteneur vivant par défaut (up réussi = service en marche, cas nominal). Un `ps_rows=[]`
        # EXPLICITE modèle « up a retourné 0 mais aucun conteneur monté » (le faux-vert à détecter).
        self.ps_rows = [{"State": "running"}] if ps_rows is None else ps_rows
        self.log_lines = log_lines or []

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

    def logs(self, name: str, workdir: Path, *, tail: int, env: dict | None = None) -> list[str]:
        self.calls.append(("logs", name, str(workdir), tail, dict(env or {})))
        return self.log_lines


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
    assert "PATH" in env                          # base allowlist du daemon (le nécessaire pour tourner)


def test_compose_env_seals_daemon_secrets_out(tmp_path: Path, monkeypatch):
    """Anti-pollution P4 : l'env passé à la CLI compose est une **allowlist** — aucun secret du daemon
    (BWS_ACCESS_TOKEN, COCKPIT_*, GITHUB_TOKEN…) n'y fuit, même s'il est présent dans l'environnement du
    daemon. Seuls la base autorisée ⊕ l'overlay explicite atteignent le build/run."""
    for leaky in ("BWS_ACCESS_TOKEN", "COCKPIT_ADMIN_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(leaky, "s3cr3t-sentinelle")
    calls, rec = _recorder()
    PodmanCompose(runner=rec).up("cockpit-x-dev", tmp_path, env={"COCKPIT_PORT": "5250"})
    env = calls[0][2]
    assert "s3cr3t-sentinelle" not in env.values()                 # le secret n'a pas fui
    for leaky in ("BWS_ACCESS_TOKEN", "COCKPIT_ADMIN_TOKEN", "GITHUB_TOKEN"):
        assert leaky not in env                                    # clé hors allowlist → absente
    assert set(env) <= set(backend_mod._COMPOSE_ENV_ALLOW) | {"COCKPIT_PORT"}   # allowlist ∪ overlay


def test_compose_engine_is_a_setting_not_code(tmp_path: Path):
    calls, rec = _recorder()
    PodmanCompose(cmd=("docker", "compose"), runner=rec).down("cockpit-x-dev", tmp_path)
    assert calls[0][0][:2] == ["docker", "compose"]   # bascule moteur = simple réglage


def test_compose_checked_raises_on_failure(tmp_path: Path):
    def rec_fail(argv, *, cwd, env, timeout):
        return RunResult(argv=list(argv), returncode=1, stdout="", stderr="nope")
    with pytest.raises(backend_mod.ComposeError):
        PodmanCompose(runner=rec_fail).up("n", tmp_path)


def test_logs_queries_engine_directly_bounded_readonly(tmp_path: Path):
    """`logs()` interroge le moteur DIRECTEMENT (`<engine> logs --tail N <cid>`), PAS `compose logs` : d'abord
    `ps` (par label) pour trouver le conteneur, puis ses logs **bornés** (jamais `--follow`)."""
    calls: list[list[str]] = []

    def rec(argv, *, cwd, env, timeout):
        calls.append(list(argv))
        if argv[1] == "ps":                                  # 1er appel : découverte du conteneur
            return RunResult(argv=list(argv), returncode=0, stdout='[{"Id": "abc123"}]', stderr="")
        return RunResult(argv=list(argv), returncode=0, stdout="", stderr="")

    PodmanCompose(cmd=("podman", "compose"), runner=rec).logs("cockpit-x-dev", tmp_path, tail=100)
    logs_argv = calls[1]
    assert logs_argv == ["podman", "logs", "--timestamps", "--tail", "100", "abc123"]
    assert "--follow" not in logs_argv and "-f" not in logs_argv    # borné : jamais un flux long-vécu


def test_logs_reads_both_stdout_and_stderr(tmp_path: Path):
    """Un handler http logge souvent sur **stderr** → `logs()` lit les deux flux (jamais un vide trompeur)."""
    def rec(argv, *, cwd, env, timeout):
        if argv[1] == "ps":
            return RunResult(argv=list(argv), returncode=0, stdout='[{"Id": "c1"}]', stderr="")
        return RunResult(argv=list(argv), returncode=0, stdout="sortie 1\n", stderr="acces stderr\n")

    lines = PodmanCompose(runner=rec).logs("n", tmp_path, tail=50)
    assert lines == ["sortie 1", "acces stderr"]             # stdout PUIS stderr, aucune perte


def test_runtime_available_resolves_engine_binary():
    """Sonde de présence du moteur (podman/docker) — `sh` résout, un binaire bidon non."""
    assert backend_mod.runtime_available(["sh"]) is True
    assert backend_mod.runtime_available(["cockpit-no-such-engine-zzz"]) is False
    assert backend_mod.runtime_available([]) is False


def test_compose_provider_available_needs_delegated_provider(tmp_path: Path):
    """`podman compose` DÉLÈGUE : `podman` présent ne suffit pas — un provider
    (`podman-compose`/`docker-compose`) doit résoudre. `docker compose` (plugin v2) → `cmd[0]` suffit."""
    bindir = tmp_path / "bin"
    bindir.mkdir()

    def mk(name: str) -> None:
        exe = bindir / name
        exe.write_text("#!/bin/sh\n:\n")
        exe.chmod(0o755)

    path = str(bindir)
    mk("podman")                                                             # moteur seul, sans provider
    assert backend_mod.compose_provider_available(("podman", "compose"), path=path) is False
    mk("podman-compose")                                                     # provider posé
    assert backend_mod.compose_provider_available(("podman", "compose"), path=path) is True
    mk("docker")
    # `docker compose` = plugin v2 embarqué → présence de `docker` (cmd[0]) suffit
    assert backend_mod.compose_provider_available(("docker", "compose"), path=path) is True
    # standalone `podman-compose` (défaut) : le binaire lui-même EST le provider (`cmd[0]`)
    assert backend_mod.compose_provider_available(("podman-compose",), path=path) is True
    assert backend_mod.compose_provider_available([], path=path) is False


def test_compose_engine_derives_container_engine():
    """Le moteur direct (`ps`/`logs`) est DÉRIVÉ du compose cmd : un standalone `*-compose` strippe son
    suffixe (→ `podman`) ; la forme sous-commande a déjà le moteur en `cmd[0]`."""
    assert backend_mod.compose_engine(("podman-compose",)) == "podman"
    assert backend_mod.compose_engine(("docker-compose",)) == "docker"
    assert backend_mod.compose_engine(("podman", "compose")) == "podman"
    assert backend_mod.compose_engine(("docker", "compose")) == "docker"
    assert backend_mod.compose_engine([]) == ""


def test_standalone_compose_up_builds_binary_argv(tmp_path: Path):
    """Le défaut `podman-compose` (standalone) : `up` construit `podman-compose -p <name> up -d --build`."""
    calls, rec = _recorder()
    PodmanCompose(cmd=("podman-compose",), runner=rec).up(
        "cockpit-x-dev", tmp_path, env={"COCKPIT_PORT": "5250"})
    assert calls[0][0] == ["podman-compose", "-p", "cockpit-x-dev", "up", "-d", "--build"]


def test_ps_uses_derived_engine_for_standalone_compose(tmp_path: Path):
    """Avec un compose STANDALONE (`podman-compose`), `ps` frappe le moteur `podman` DIRECTEMENT (pas
    `podman-compose ps`, qui ne fait pas le `--format json` moteur) — via `compose_engine`."""
    calls: list[list[str]] = []

    def cap(argv, *, cwd, env, timeout):
        calls.append(list(argv))
        return RunResult(argv=list(argv), returncode=0, stdout='[{"State": "running"}]', stderr="")

    PodmanCompose(cmd=("podman-compose",), runner=cap).ps("cockpit-x-dev", tmp_path)
    assert calls[0][0] == "podman"                                   # moteur dérivé, PAS `podman-compose`
    assert calls[0][1] == "ps" and "--format" in calls[0]
    assert "label=com.docker.compose.project=cockpit-x-dev" in calls[0]


def test_missing_engine_raises_compose_error_not_filenotfound(tmp_path: Path):
    """Moteur absent (binaire introuvable) → `ComposeError` **actionnable** (précondition), PAS un
    `FileNotFoundError` brut : le runner par défaut sonde le PATH avant le sous-process. C'est ce qui rend
    `deploy status` gracieux (l'engine catch `ComposeError` → `unhealthy`, jamais une stacktrace)."""
    # runner par DÉFAUT (which réel), binaire de moteur introuvable
    engine_cli = PodmanCompose(cmd=("cockpit-no-such-engine-zzz", "compose"))
    with pytest.raises(backend_mod.ComposeError, match="runtime conteneur absent"):
        engine_cli.ps("cockpit-x-dev", tmp_path)


def test_ps_queries_container_engine_directly_by_compose_label(tmp_path: Path):
    """`ps()` interroge le MOTEUR directement (`<engine> ps --format json --filter label=…`), PAS `compose ps`
    (podman-compose 1.0.6 ne supporte ni `--format json` ni `-a`, et re-parse le compose). Filtre par
    `com.docker.compose.project` (label posé par docker ET podman) → cross-backend, sans re-parse compose."""
    calls: list[list[str]] = []

    def cap(argv, *, cwd, env, timeout):
        calls.append(list(argv))
        return RunResult(argv=list(argv), returncode=0, stdout='[{"State": "running"}]', stderr="")

    rows = PodmanCompose(cmd=("podman", "compose"), runner=cap).ps("cockpit-x-dev", tmp_path)
    argv = calls[0]
    assert argv[0] == "podman" and argv[1] == "ps"           # moteur direct, jamais `compose ps`
    assert "--format" in argv and "json" in argv
    assert "label=com.docker.compose.project=cockpit-x-dev" in argv
    assert rows == [{"State": "running"}]                    # état honnête parsé (is_running le lit)


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


@pytest.mark.parametrize("dead_rows", [[], [{"State": "exited"}], [{"Status": "Exited (1)"}]])
def test_deploy_false_green_fails_close_when_no_container_running(ctx, dead_rows):
    """Faux-vert : `podman-compose up --build` retourne exit 0 même quand le BUILD échoue (ou que le
    conteneur sort aussitôt) → `up()` ne lève pas. `deploy` DOIT sonder l'état réel (`ps`) et fail-close en
    `unhealthy` + lever, jamais poser `running` sur un déploiement mort (cf. deploy-up-false-green)."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend(ps_rows=dead_rows)                     # up « réussit » (exit 0), 0 conteneur vivant
    with pytest.raises(ValueError, match="aucun conteneur en marche"):
        engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=be)
    assert _dep(conn, "svc")["status"] == "unhealthy"       # jamais un faux-vert
    assert be.calls[0][0] == "up" and any(c[0] == "ps" for c in be.calls)   # up tenté, PUIS état réel sondé


def test_deploy_unhealthy_when_ps_introspection_fails(ctx):
    """Si l'introspection post-`up` échoue (`ps` lève ComposeError), `deploy` fail-close en `unhealthy` +
    lève, plutôt que de poser `running` en aveugle."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")

    class _PsBoom(FakeBackend):
        def ps(self, name: Path, workdir: Path, *, env: dict | None = None) -> list[dict]:
            self.calls.append(("ps", name, str(workdir), dict(env or {})))
            raise backend_mod.ComposeError("ps injoignable")

    with pytest.raises(ValueError, match="inintrospectable"):
        engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=_PsBoom())
    assert _dep(conn, "svc")["status"] == "unhealthy"


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
    be = FakeBackend()                                     # deploy nominal (conteneur vivant)
    engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=be)
    be.ps_rows = []                                        # puis le conteneur meurt → status réconcilie
    assert engine.status(conn, settings, slug="svc", branch="dev", backend=be)["status"] == "stopped"


def test_logs_returns_lines_of_a_deployed_service(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend(log_lines=["2026-07-13T10:00:00Z boot", "2026-07-13T10:00:01Z serving :8000"])
    engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=be)
    out = engine.logs(conn, settings, slug="svc", branch="dev", backend=be)
    assert out["lines"] == ["2026-07-13T10:00:00Z boot", "2026-07-13T10:00:01Z serving :8000"]


def test_logs_are_honest_empty_when_never_deployed(ctx):
    """Un déploiement jamais monté (`no_deploy`) → `lines: []` (vide honnête), et le backend n'est JAMAIS
    appelé (pas de `compose logs` sur un projet sans workdir) — jamais une erreur ni un faux-vert."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend(log_lines=["ne devrait pas remonter"])
    assert engine.logs(conn, settings, slug="svc", branch="dev", backend=be) == {"lines": []}
    assert be.calls == []                                        # aucun appel moteur


def test_logs_clamps_tail_to_the_hard_bound(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    be = FakeBackend()
    engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=be)
    engine.logs(conn, settings, slug="svc", branch="dev", tail=10_000, backend=be)   # au-delà de la borne
    logs_call = next(c for c in be.calls if c[0] == "logs")
    assert logs_call[3] == engine._LOGS_TAIL_MAX                 # tail clampé à la borne dure


def test_logs_rejects_invalid_branch_and_unknown_project(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    with pytest.raises(ValueError, match="branche invalide"):
        engine.logs(conn, settings, slug="svc", branch="prod", backend=FakeBackend())
    with pytest.raises(KeyError):
        engine.logs(conn, settings, slug="ghost", branch="dev", backend=FakeBackend())


def test_deploy_rejects_invalid_branch_and_unknown_project(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    with pytest.raises(ValueError, match="branche invalide"):
        engine.deploy(conn, settings, slug="svc", branch="prod", git=FakeGit(), backend=FakeBackend())
    with pytest.raises(KeyError):
        engine.deploy(conn, settings, slug="ghost", branch="dev", git=FakeGit(), backend=FakeBackend())


class _NoComposeGit(FakeGit):
    """Arbre archivé SANS aucun fichier compose — calque d'un type non-service (`cli-tool`/`generic`)."""

    def archive(self, sot: Path, ref: str, dest_dir: Path) -> None:
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        (Path(dest_dir) / "README.md").write_text("pas de service déployable\n")
        self.archived.append((ref, str(dest_dir)))


def test_deploy_refuses_tree_without_compose(ctx):
    """Pré-vol honnête (P3) : un arbre sans compose.yaml (type non-service) → `ValueError` clair (→ 400)
    + `unhealthy`, et le backend n'est JAMAIS appelé (pas d'erreur podman opaque plus loin)."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="cli")
    be = FakeBackend()
    with pytest.raises(ValueError, match="n'expose pas de service déployable"):
        engine.deploy(conn, settings, slug="cli", branch="dev", git=_NoComposeGit(), backend=be)
    assert _dep(conn, "cli")["status"] == "unhealthy"       # jamais un faux-vert
    assert be.calls == []                                   # refus AVANT tout appel moteur


# -- preview-deploy (Tier-1.5 pré-merge) : worktree éphémère, hors table deployments ---------------

def _seed_worktree(settings, slug: str, feature: str, *, compose: bool = True) -> Path:
    from cockpit.dispatch.worktree import worktree_path_for
    wt = worktree_path_for(settings, slug, feature)
    wt.mkdir(parents=True, exist_ok=True)
    if compose:
        (wt / "compose.yaml").write_text("services: {}\n")
    return wt


def test_deploy_preview_serves_worktree_on_deploy_pool_port(ctx):
    """`deploy_preview` up le service depuis le WORKTREE de la feature (pas `deploy_dir`), sur un port du pool
    deploy, avec un nom compose slash-safe — sans toucher la table `deployments`."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    wt = _seed_worktree(settings, "svc", "feat")
    be = FakeBackend()
    prev = engine.deploy_preview(conn, settings, slug="svc", feature="feat", backend=be)
    assert engine.DEPLOY_RANGE[0] <= prev["port"] <= engine.DEPLOY_RANGE[1]       # pool deploy
    assert prev["url"] == f"http://127.0.0.1:{prev['port']}"
    assert prev["name"] == "cockpit-svc-preview-feat"                             # slash-safe (feature kebab)
    assert prev["workdir"] == str(wt)
    op, name, workdir, env = be.calls[0]
    assert (op, name) == ("up", "cockpit-svc-preview-feat")
    assert workdir == str(wt)                                                     # up pointe le WORKTREE
    assert env["COCKPIT_PORT"] == str(prev["port"])


def test_deploy_preview_port_distinct_from_deploy_and_worktree(ctx):
    """Le port preview est une 3ᵉ clé (`preview:<feature>`) — jamais celui du deploy `dev` ni du worktree."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    _seed_worktree(settings, "svc", "feat")
    dep = engine.deploy(conn, settings, slug="svc", branch="dev", git=FakeGit(), backend=FakeBackend())
    wt_res = ports.reserve(conn, project="svc", purpose="worktree:feat")
    prev = engine.deploy_preview(conn, settings, slug="svc", feature="feat", backend=FakeBackend())
    assert prev["port"] != dep["port"] and prev["port"] != wt_res["port"]


def test_teardown_preview_downs_and_releases_port(ctx):
    """`teardown_preview` down le compose ET relâche le port — le worktree n'est PAS supprimé."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    wt = _seed_worktree(settings, "svc", "feat")
    be = FakeBackend()
    engine.deploy_preview(conn, settings, slug="svc", feature="feat", backend=be)
    engine.teardown_preview(conn, settings, slug="svc", feature="feat", backend=be)
    assert (be.calls[-1][0], be.calls[-1][1]) == ("down", "cockpit-svc-preview-feat")
    assert wt.exists()                                                           # worktree préservé
    re_prev = engine.deploy_preview(conn, settings, slug="svc", feature="feat", backend=FakeBackend())
    assert engine.DEPLOY_RANGE[0] <= re_prev["port"] <= engine.DEPLOY_RANGE[1]   # release n'a pas bloqué


def test_deploy_preview_refuses_missing_worktree_or_compose(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    with pytest.raises(ValueError, match="worktree absent"):
        engine.deploy_preview(conn, settings, slug="svc", feature="ghost", backend=FakeBackend())
    _seed_worktree(settings, "svc", "nocompose", compose=False)
    with pytest.raises(ValueError, match="n'expose pas de service déployable"):
        engine.deploy_preview(conn, settings, slug="svc", feature="nocompose", backend=FakeBackend())


# -- autoverify : preview-deploy + preuve de rendu, auto-suffisant (Tier-1.5 pré-merge) -------------

def test_autoverify_feature_writes_fresh_verdict_and_always_tears_down(ctx, monkeypatch):
    """`autoverify_feature` orchestre : preview-deploy (worktree) → attend → markers DÉCLARÉS → preuve →
    verdict SHA-bound frais → teardown. Les markers viennent de `.cockpit/verify-markers.json` du worktree."""
    from cockpit.gate import verify
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    wt = _seed_worktree(settings, "svc", "feat")
    (wt / ".cockpit").mkdir()
    (wt / ".cockpit" / "verify-markers.json").write_text('{"markers": ["Accueil"]}', encoding="utf-8")
    seen: dict = {}
    monkeypatch.setattr(verify, "_wait_http_ready", lambda url, **k: True)

    def _fake_verify_target(settings_, url, markers, *, name=None, **k):
        seen.update(url=url, markers=markers, name=name)
        return {"name": name, "ok": True, "found": markers, "missing": []}

    monkeypatch.setattr(verify, "verify_target", _fake_verify_target)
    be = FakeBackend()
    verdict = verify.autoverify_feature(conn, settings, project="svc", feature="feat", sha="abc123",
                                        backend=be)
    assert verdict["ok"] is True and verdict["reviewed_sha"] == "abc123"
    assert seen["markers"] == ["Accueil"]                                    # cibles = markers déclarés
    assert seen["url"].startswith("http://127.0.0.1:")
    assert ("down", "cockpit-svc-preview-feat") in [(c[0], c[1]) for c in be.calls]
    assert verify.status(settings, "svc", "feat", current_sha="abc123")["fresh"] is True


def test_autoverify_feature_tears_down_even_when_verify_raises(ctx, monkeypatch):
    """Le `finally` démonte TOUJOURS (jamais de fuite de port/conteneur), même si la vérif explose."""
    from cockpit.gate import verify
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc")
    _seed_worktree(settings, "svc", "feat")
    monkeypatch.setattr(verify, "_wait_http_ready", lambda url, **k: True)

    def _boom(*a, **k):
        raise RuntimeError("runner explose")

    monkeypatch.setattr(verify, "verify_target", _boom)
    be = FakeBackend()
    with pytest.raises(RuntimeError):
        verify.autoverify_feature(conn, settings, project="svc", feature="feat", sha="s", backend=be)
    assert ("down", "cockpit-svc-preview-feat") in [(c[0], c[1]) for c in be.calls]
    re_prev = engine.deploy_preview(conn, settings, slug="svc", feature="feat", backend=FakeBackend())
    assert engine.DEPLOY_RANGE[0] <= re_prev["port"] <= engine.DEPLOY_RANGE[1]  # port relâché → re-preview OK


def test_wait_http_ready_polls_bounded(monkeypatch):
    """Toute réponse HTTP (même 4xx/5xx) = le serveur écoute → prêt ; refus de connexion → borné → False."""
    import urllib.error
    import urllib.request

    from cockpit.gate import verify
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=5: object())
    assert verify._wait_http_ready("http://x/", timeout_s=1) is True

    def _http_err(url, timeout=5):
        raise urllib.error.HTTPError(url, 503, "down", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _http_err)
    assert verify._wait_http_ready("http://x/", timeout_s=1) is True

    def _refused(url, timeout=5):
        raise urllib.error.URLError("connexion refusée")

    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    assert verify._wait_http_ready("http://x/", timeout_s=0.05, interval_s=0.01) is False


# -- anti-pollution P4 : frontière FS structurelle + pools de ports disjoints ----------------------

def test_deploy_dirs_are_per_project_isolated(tmp_path: Path):
    """Le contexte de build/run vit sous `<projects_root>/<slug>/deploy/<branch>` : chaque (projet, branche)
    a son sous-arbre PROPRE, aucun n'est niché dans un autre → un service ne voit jamais l'arbre d'un voisin
    (l'image ne reçoit que le `git archive` de SON SoT via `COPY . .`)."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    a_dev = deploy_dir_for(settings, "alpha", "dev")
    b_dev = deploy_dir_for(settings, "beta", "dev")
    a_main = deploy_dir_for(settings, "alpha", "main")
    dirs = [a_dev, b_dev, a_main]
    assert len({str(d) for d in dirs}) == 3                       # trois chemins distincts
    for d in dirs:
        assert settings.projects_root in d.parents               # borné sous projects_root
    # aucun répertoire de déploiement n'est un ancêtre d'un autre (pas d'inclusion inter-projets)
    for i, d in enumerate(dirs):
        for j, other in enumerate(dirs):
            if i != j:
                assert d not in other.parents


def test_deploy_and_worktree_pools_are_disjoint():
    """Invariant P2 verrouillé (P4) : le pool de ports **deploy** et le pool **worktree** ne se recouvrent
    jamais → une réservation de service ne peut pas coïncider avec un port de worktree (`UNIQUE(port)` global
    fait le reste au sein d'un pool)."""
    d_lo, d_hi = engine.DEPLOY_RANGE
    w_lo, w_hi = ports.DEFAULT_RANGE
    assert d_hi < w_lo or w_hi < d_lo                             # intervalles disjoints


# -- routes POST du lifecycle (backend factice injecté au défaut de l'engine) ----------------------

def test_deploy_route_up_reflected_then_down(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose", lambda **_kw: FakeBackend())
    # type-service : le SoT semé porte un compose.yaml (P3) → le pré-vol de `deploy` passe (le backend
    # est un fake, mais `git.archive` est RÉEL → l'arbre doit vraiment contenir la config de run).
    assert c.post("/api/projects", json={"slug": "web", "project_type": "service-api"}).status_code == 201
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


# -- routes GET d'observabilité (P5) : status live + logs bornés --------------------------------------

def test_status_route_reflects_live_container_state(client, monkeypatch):
    """`GET .../status` réconcilie avec l'état live (`compose ps`) : un conteneur up → `running`."""
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose",
                        lambda **_kw: FakeBackend(ps_rows=[{"State": "running"}]))
    assert c.post("/api/projects", json={"slug": "web", "project_type": "service-api"}).status_code == 201
    c.post("/api/projects/web/deployments/dev/up")
    r = c.get("/api/projects/web/deployments/dev/status")
    assert r.status_code == 200 and r.json()["deployment"]["status"] == "running"


def test_logs_route_returns_service_lines(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose",
                        lambda **_kw: FakeBackend(log_lines=["boot", "serving"]))
    assert c.post("/api/projects", json={"slug": "web", "project_type": "service-api"}).status_code == 201
    c.post("/api/projects/web/deployments/dev/up")
    r = c.get("/api/projects/web/deployments/dev/logs?tail=100")
    assert r.status_code == 200
    body = r.json()
    assert body["branch"] == "dev" and body["lines"] == ["boot", "serving"]


def test_logs_route_honest_empty_when_never_deployed(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose", lambda **_kw: FakeBackend(log_lines=["x"]))
    assert c.post("/api/projects", json={"slug": "web", "project_type": "service-api"}).status_code == 201
    r = c.get("/api/projects/web/deployments/dev/logs")     # jamais déployé → vide honnête
    assert r.status_code == 200 and r.json()["lines"] == []


def test_logs_route_rejects_out_of_range_tail(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose", lambda **_kw: FakeBackend())
    c.post("/api/projects", json={"slug": "web", "project_type": "service-api"})
    assert c.get("/api/projects/web/deployments/dev/logs?tail=99999").status_code == 422   # borné par Query


def test_observability_routes_404_unknown_project(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(engine, "PodmanCompose", lambda **_kw: FakeBackend())
    assert c.get("/api/projects/ghost/deployments/dev/status").status_code == 404
    assert c.get("/api/projects/ghost/deployments/dev/logs").status_code == 404
