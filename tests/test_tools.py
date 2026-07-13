"""Tests de `cockpit.tools` — provisionnement hôte-niveau de l'outillage déclaré par les bundles.

Seams PURS (chemins/PATH/plan) testés sans subprocess ; `install_tools` avec un runner INJECTÉ qui
matérialise les binaires attendus, pour prouver symlinks, idempotence, fail-loud et auth transitoire —
jamais un vrai pip/nodeenv (lents, réseau : prouvés à la vérif install fraîche)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cockpit import tools
from cockpit.config import Settings
from cockpit.core.run import RunResult


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


# -- seams PURS -------------------------------------------------------------------------------------

def test_path_layout_under_cockpit_home(settings):
    assert tools.tools_root(settings) == settings.home / "tools"
    assert tools.tools_venv(settings) == settings.home / "tools" / "venv"
    assert tools.nodeenv_prefix(settings) == settings.home / "tools" / "nodeenv"
    assert tools.tools_bin(settings) == settings.home / "tools" / "bin"


def test_tools_env_prepends_bin_and_is_pure(settings):
    base = {"PATH": "/usr/bin:/bin", "FOO": "bar"}
    env = tools.tools_env(settings, base=base)
    assert env["PATH"] == f"{tools.tools_bin(settings)}:/usr/bin:/bin"   # tools/bin EN TÊTE
    assert env["FOO"] == "bar"                       # reste de l'env préservé
    assert base["PATH"] == "/usr/bin:/bin"           # base NON mutée (pur)


def test_tools_env_empty_path(settings):
    env = tools.tools_env(settings, base={})
    assert env["PATH"] == str(tools.tools_bin(settings))                # pas de ':' pendant


def test_install_plan_covers_maps_quality_and_node(settings):
    plan = tools.install_plan(settings)
    names = [s["name"] for s in plan]
    assert names == ["pip-tools", "pip-nodeenv", "nodeenv"]
    pip_tools = plan[0]["argv"]
    # les 3 cartes en git+<url>@main + les 3 outils qualité, tous dans un seul pip install
    for repo_url in tools.MAP_REPOS.values():
        assert f"git+{repo_url}@{tools.MAP_REF}" in pip_tools
    for q in tools.PY_QUALITY:
        assert q in pip_tools
    assert plan[2]["argv"][0].endswith("/nodeenv") and f"--node={tools.NODE_VERSION}" in plan[2]["argv"]


def test_symlink_sources_split_venv_and_node(settings):
    srcs = tools._symlink_sources(settings)
    assert srcs["codemap"] == tools.tools_venv(settings) / "bin" / "codemap"
    assert srcs["node"] == tools.nodeenv_prefix(settings) / "bin" / "node"
    assert set(srcs) == {"codemap", "docsmap", "frontmap", "ruff", "pytest", "mypy", "node", "npm", "npx"}


# -- install_tools (runner injecté qui matérialise les binaires) ------------------------------------

def _materializing_runner(settings, *, captured_envs=None, fail_on=None):
    """Runner fake : matérialise les binaires que chaque étape produirait (pour que la phase symlink voie
    des sources réelles). `fail_on` = nom de step à faire échouer (rc 1). Capture l'env par step si fourni."""
    venv_bin = tools.tools_venv(settings) / "bin"
    node_bin = tools.nodeenv_prefix(settings) / "bin"

    def touch(p: Path) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\n", encoding="utf-8")
        p.chmod(0o755)

    def runner(argv, *, env=None, timeout=None):
        exe = argv[0]
        step = ("venv" if "venv" in argv and "-m" in argv
                else "nodeenv" if exe.endswith("/nodeenv")
                else "pip-nodeenv" if "nodeenv" in argv
                else "pip-tools")
        if captured_envs is not None:
            captured_envs[step] = env
        if fail_on == step:
            return RunResult(argv=list(argv), returncode=1, stdout="", stderr="boom")
        if step == "venv":
            venv_bin.mkdir(parents=True, exist_ok=True)
        elif step == "pip-tools":
            for name in ("codemap", "docsmap", "frontmap", "ruff", "pytest", "mypy"):
                touch(venv_bin / name)
        elif step == "pip-nodeenv":
            touch(venv_bin / "nodeenv")
        elif step == "nodeenv":
            for name in ("node", "npm", "npx"):
                touch(node_bin / name)
        return RunResult(argv=list(argv), returncode=0, stdout="ok", stderr="")

    return runner


def test_install_tools_happy_path_exposes_all_bins(settings):
    report = tools.install_tools(settings, runner=_materializing_runner(settings))
    assert report["ok"] is True
    bin_dir = tools.tools_bin(settings)
    for name in ("codemap", "docsmap", "frontmap", "ruff", "pytest", "mypy", "node", "npm", "npx"):
        link = bin_dir / name
        assert link.is_symlink() and link.resolve().exists()            # exposé ET pointe une source réelle
    assert set(report["symlinks"]) == set(tools._symlink_sources(settings))


def test_install_tools_is_idempotent(settings):
    r1 = tools.install_tools(settings, runner=_materializing_runner(settings))
    # 2e run : remplace les symlinks existants sans erreur
    r2 = tools.install_tools(settings, runner=_materializing_runner(settings))
    assert r1["ok"] and r2["ok"]
    assert (tools.tools_bin(settings) / "codemap").is_symlink()


def test_install_tools_fail_loud_aborts_before_symlink(settings):
    report = tools.install_tools(settings, runner=_materializing_runner(settings, fail_on="pip-tools"))
    assert report["ok"] is False
    assert "pip-tools" in report["error"]
    assert not (tools.tools_bin(settings) / "codemap").exists()   # pas de symlink sur un demi-provisioning
    failed = [s for s in report["steps"] if not s["ok"]]
    assert failed and failed[0]["name"] == "pip-tools"


def test_install_tools_missing_binary_after_green_install_is_loud(settings):
    """Étapes vertes mais une source absente (install incohérente) → fail-loud, pas de faux-vert."""
    def runner(argv, *, env=None, timeout=None):
        if "venv" in argv and "-m" in argv:
            (tools.tools_venv(settings) / "bin").mkdir(parents=True, exist_ok=True)
        return RunResult(argv=list(argv), returncode=0, stdout="ok", stderr="")   # ne matérialise AUCUN bin
    report = tools.install_tools(settings, runner=runner)
    assert report["ok"] is False and "absent après install" in report["error"]


def test_install_tools_injects_credential_env_for_private_maps(settings):
    """Un token fourni → les étapes pip tournent sous un env porteur du `insteadOf` (token JAMAIS en argv)."""
    envs: dict = {}
    tools.install_tools(settings, token="ghp_secret",
                        runner=_materializing_runner(settings, captured_envs=envs))
    pip_env = envs["pip-tools"]
    # credential_env pose un url.<embed x-access-token:token>.insteadOf via GIT_CONFIG_KEY_n
    keys = {k: v for k, v in pip_env.items() if k.startswith("GIT_CONFIG_KEY_")}
    assert any("x-access-token:ghp_secret@github.com" in v for v in keys.values())
    assert pip_env.get("GIT_TERMINAL_PROMPT") == "0"


def test_install_tools_no_token_runs_ambient(settings):
    """Sans token (repos publics à terme) → aucun insteadOf injecté ; l'install tourne en env ambiant."""
    envs: dict = {}
    tools.install_tools(settings, runner=_materializing_runner(settings, captured_envs=envs))
    assert not any(k.startswith("GIT_CONFIG_KEY_") for k in envs["pip-tools"])
