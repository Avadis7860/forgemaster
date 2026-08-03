"""Tests de `cockpit.tools` — provisionnement hôte-niveau de l'outillage déclaré par les bundles.

Seams PURS (chemins/PATH/plan) testés sans subprocess ; `install_tools` avec un runner INJECTÉ qui
matérialise les binaires attendus, pour prouver symlinks, idempotence, fail-loud et clone anonyme —
jamais un vrai pip/nodeenv (lents, réseau : prouvés à la vérif install fraîche)."""
from __future__ import annotations

import os
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


def test_tools_env_prepends_bin_then_local_bin_and_is_pure(settings):
    base = {"PATH": "/usr/bin:/bin", "HOME": "/home/x", "FOO": "bar"}
    env = tools.tools_env(settings, base=base)
    # tools/bin EN TÊTE, puis $HOME/.local/bin (où `--with-claude` pose `claude`), puis le PATH de base.
    assert env["PATH"] == f"{tools.tools_bin(settings)}:/home/x/.local/bin:/usr/bin:/bin"
    assert env["FOO"] == "bar"                       # reste de l'env préservé
    assert base["PATH"] == "/usr/bin:/bin"           # base NON mutée (pur)


def test_tools_env_empty_path(settings):
    env = tools.tools_env(settings, base={"HOME": "/home/x"})
    assert env["PATH"] == f"{tools.tools_bin(settings)}:/home/x/.local/bin"   # bin + local/bin


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
    assert set(srcs) == {"codemap", "docsmap", "frontmap",
                         "ruff", "pytest", "mypy", "node", "npm", "npx"}


def test_taskmap_not_host_provisioned():
    """task-map = moteur central (`taskmap.core`, importé en-process, vendoré au wheel), PAS une carte de
    contenu par-projet → jamais provisionné en host-tool. Verrou anti-régression du retire : ni pip-installé
    (`MAP_REPOS`) ni exposé/gaté (`_VENV_BINS`/`HOST_TOOLS`). La LIB, elle, reste importable par ailleurs."""
    assert "task-map" not in tools.MAP_REPOS
    assert "taskmap" not in tools._VENV_BINS
    assert "taskmap" not in tools.HOST_TOOLS


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


def test_install_tools_adds_nothing_but_the_prompt_guard(settings):
    """Les 3 cartes sont PUBLIQUES : l'install n'AJOUTE aucun credential — elle ajoute `GIT_TERMINAL_PROMPT`
    et rien d'autre.

    Garde de non-régression du chemin d'install. L'assertion porte sur le **delta** avec l'ambiant, pas sur
    l'absence de tout token dans l'env : ce que git lit du `.gitconfig` de l'utilisateur reste à lui. Ce qui
    est interdit, c'est que le cockpit compose un `insteadOf` — tant que c'était possible, chaque E2E
    tournait sous une configuration qu'aucun utilisateur n'aura jamais."""
    envs: dict = {}
    tools.install_tools(settings, runner=_materializing_runner(settings, captured_envs=envs))
    for name, env in envs.items():
        added = {k: v for k, v in env.items() if os.environ.get(k) != v}
        assert set(added) <= {"GIT_TERMINAL_PROMPT"}, f"l'étape {name} ajoute à l'env : {sorted(added)}"


def test_install_tools_disables_git_prompt(settings):
    """`GIT_TERMINAL_PROMPT=0` sur les étapes pip : un repo injoignable échoue NET au lieu de pendre sur un
    prompt de credentials jusqu'au timeout de 900 s (le mode d'échec le plus opaque de cette install)."""
    envs: dict = {}
    tools.install_tools(settings, runner=_materializing_runner(settings, captured_envs=envs))
    assert envs["pip-tools"].get("GIT_TERMINAL_PROMPT") == "0"


def test_install_tools_takes_no_credential_argument(settings):
    """La signature ne porte plus `token`/`token_ref` : le chemin d'auth est RETIRÉ, pas rendu optionnel."""
    import inspect
    params = inspect.signature(tools.install_tools).parameters
    assert "token" not in params and "token_ref" not in params
