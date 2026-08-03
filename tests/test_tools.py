"""Tests de `cockpit.tools` — provisionnement hôte-niveau de l'outillage déclaré par les bundles.

Seams PURS (chemins/PATH/plan) testés sans subprocess ; `install_tools` avec un runner INJECTÉ qui
matérialise les binaires attendus, pour prouver symlinks, idempotence, fail-loud et clone anonyme —
jamais un vrai pip/nodeenv (lents, réseau : prouvés à la vérif install fraîche)."""
from __future__ import annotations

import json
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


# -- provenance des cartes SERVIES (lecture locale de direct_url.json, zéro réseau) -------------------

_SHA_A = "775117a03d761abe80652a30cceae30f989be82e"      # relevé sur la VM 9311 le 2026-08-03
_SHA_B = "d04c2770000000000000000000000000000000aa"


def _seed_dist(settings, name: str, payload, *, version: str = "0.1.0") -> Path:
    """Matérialise `<name>-<version>.dist-info` dans le site-packages du venv d'outils. `payload` : un dict
    (sérialisé), une str (écrite telle quelle — JSON invalide), ou None (aucun `direct_url.json`)."""
    sp = tools.tools_venv(settings) / "lib" / "python3.12" / "site-packages"
    d = sp / f"{name.replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        body = json.dumps(payload) if isinstance(payload, dict) else payload
        (d / "direct_url.json").write_text(body, encoding="utf-8")
    return d


def _vcs(sha: str, ref: str = "main") -> dict:
    return {"url": "https://github.com/Avadis7860/code-map.git",
            "vcs_info": {"commit_id": sha, "requested_revision": ref, "vcs": "git"}}


def test_maps_provenance_reads_the_served_commit(settings):
    """Le cas réel : pip a posé `direct_url.json` à l'install, on LIT le `commit_id` résolu. Aucun tampon
    n'est écrit par le cockpit — la provenance existait déjà sur la machine."""
    _seed_dist(settings, "code-map", _vcs(_SHA_A))
    entry = next(m for m in tools.maps_provenance(settings) if m["name"] == "code-map")
    assert entry["sha"] == _SHA_A
    assert entry["requested_ref"] == "main"
    assert entry["source"] == "vcs"
    assert entry["reason"] is None


def test_maps_provenance_covers_the_three_host_maps_in_order(settings):
    """Les 3 cartes hôte, dans l'ordre de `MAP_REPOS` — et task-map n'en est pas (moteur vendoré)."""
    names = [m["name"] for m in tools.maps_provenance(settings)]
    assert names == list(tools.MAP_REPOS)
    assert "task-map" not in names


def test_maps_provenance_without_a_tools_venv_is_a_valid_state(settings):
    """Un cockpit en checkout dev n'a pas de `tools/venv` : ce n'est PAS une erreur. On rend les 3 entrées
    avec leur raison, plutôt que de lever et de faire tomber `/api/version`."""
    entries = tools.maps_provenance(settings)
    assert len(entries) == 3
    assert all(m["sha"] is None and m["source"] == "unknown" and m["reason"] for m in entries)


def test_maps_provenance_editable_install_says_so(settings):
    """Install depuis un répertoire (éditable) : aucun SHA à servir, et on le DIT au lieu d'en deviner un."""
    _seed_dist(settings, "docs-map", {"url": "file:///home/dev/docs-map", "dir_info": {"editable": True}})
    entry = next(m for m in tools.maps_provenance(settings) if m["name"] == "docs-map")
    assert entry["sha"] is None and entry["source"] == "local-dir" and "aucun SHA" in entry["reason"]


def test_maps_provenance_rejects_a_commit_id_that_is_not_a_sha(settings):
    """Un `commit_id` présent mais non-SHA n'est PAS servi comme une identité : mieux vaut avouer."""
    _seed_dist(settings, "front-map", _vcs("pas-un-sha"))
    entry = next(m for m in tools.maps_provenance(settings) if m["name"] == "front-map")
    assert entry["sha"] is None and "n'est pas un SHA" in entry["reason"]


def test_maps_provenance_unreadable_json_is_admitted(settings):
    _seed_dist(settings, "code-map", "{ceci n'est pas du json")
    entry = next(m for m in tools.maps_provenance(settings) if m["name"] == "code-map")
    assert entry["sha"] is None and "illisible" in entry["reason"]


def test_maps_provenance_without_direct_url_is_admitted(settings):
    """Installée depuis un index/wheel : PEP 610 n'enregistre alors pas d'origine — dit, pas deviné."""
    _seed_dist(settings, "code-map", None)
    entry = next(m for m in tools.maps_provenance(settings) if m["name"] == "code-map")
    assert entry["sha"] is None and "direct_url.json" in entry["reason"]


def test_no_branch_ever_returns_a_silent_none(settings):
    """L'INVARIANT du contrat de dégradation, testé pour lui-même : sur TOUTES les formes rencontrables,
    un `sha=None` s'accompagne TOUJOURS d'un `reason`. Un None muet ferait croire à une absence bénigne."""
    _seed_dist(settings, "code-map", _vcs(_SHA_A))                                    # ok
    _seed_dist(settings, "docs-map", {"url": "file:///x", "dir_info": {}})            # local-dir
    _seed_dist(settings, "front-map", {"url": "https://x", "archive_info": {}})       # ni vcs ni dir
    for entry in tools.maps_provenance(settings):
        assert entry["sha"] is not None or entry["reason"], entry


# -- sonde de fraîcheur : seams purs ------------------------------------------------------------------

def test_check_plan_is_one_ls_remote_per_map_and_pure(settings):
    plan = tools.check_plan(settings)
    assert [s["name"] for s in plan] == list(tools.MAP_REPOS)
    for step in plan:
        assert step["argv"][:2] == ["git", "ls-remote"]
        assert step["argv"][-1] == tools.MAP_REF
    assert not tools.tools_venv(settings).exists()          # PUR : n'a rien créé


def test_parse_ls_remote_finds_the_ref(settings):
    out = f"{_SHA_A}\trefs/heads/main\n{_SHA_B}\trefs/heads/dev\n"
    assert tools.parse_ls_remote(out) == _SHA_A


def test_parse_ls_remote_absent_ref_is_none(settings):
    assert tools.parse_ls_remote(f"{_SHA_A}\trefs/heads/dev\n") is None


def test_parse_ls_remote_rejects_a_non_sha(settings):
    assert tools.parse_ls_remote("pouet\trefs/heads/main\n") is None


def test_compare_up_to_date(settings):
    served = [{"name": "code-map", "sha": _SHA_A, "reason": None}]
    assert tools.compare(served, {"code-map": _SHA_A})[0]["state"] == "up-to-date"


def test_compare_differs_writes_both_shas(settings):
    """Le verdict porte les DEUX SHA : c'est ce qui rend l'écart actionnable sans ssh sur la machine."""
    served = [{"name": "code-map", "sha": _SHA_A, "reason": None}]
    e = tools.compare(served, {"code-map": _SHA_B})[0]
    assert e["state"] == "differs" and e["served"] == _SHA_A and e["remote"] == _SHA_B


def test_compare_never_invents_a_commit_count(settings):
    """`ls-remote` ne rend que des réfs — compter exigerait de rapatrier l'historique. On dit LESQUELLES
    ont bougé, jamais « de N commits ». Un chiffre faux retirerait le doute qui doit déclencher la vérif."""
    served = [{"name": "code-map", "sha": _SHA_A, "reason": None}]
    e = tools.compare(served, {"code-map": _SHA_B})[0]
    assert not {"behind_by", "behind", "count", "commits"} & set(e)


def test_compare_unreachable_upstream_is_unknown_not_up_to_date(settings):
    """Le faux-vert qu'on refuse : amont injoignable ⇒ « pas pu vérifier », JAMAIS « à jour »."""
    served = [{"name": "code-map", "sha": _SHA_A, "reason": None}]
    e = tools.compare(served, {"code-map": None})[0]
    assert e["state"] == "unknown" and e["reason"]


def test_compare_carries_the_map_reason_when_nothing_is_served(settings):
    served = [{"name": "code-map", "sha": None, "reason": "pas installée"}]
    e = tools.compare(served, {"code-map": _SHA_A})[0]
    assert e["state"] == "unknown" and e["reason"] == "pas installée"


def test_overall_state_differs_wins_over_unknown(settings):
    entries = [{"state": "up-to-date"}, {"state": "unknown"}, {"state": "differs"}]
    assert tools.overall_state(entries) == "differs"


def test_overall_state_unknown_never_becomes_up_to_date(settings):
    entries = [{"state": "up-to-date"}, {"state": "unknown"}]
    assert tools.overall_state(entries) == "unknown"


def test_overall_state_all_verified(settings):
    assert tools.overall_state([{"state": "up-to-date"}] * 3) == "up-to-date"


# -- sonde de fraîcheur : exécution (runner injecté) --------------------------------------------------

def _ls_remote_runner(shas: dict, *, captured_envs=None, boom: bool = False):
    """Runner fake pour `check_tools` : rend un `ls-remote` par carte. `shas[name] is None` → rc 1."""
    def runner(argv, *, env=None, timeout=None):
        url = argv[2]
        name = next(n for n, u in tools.MAP_REPOS.items() if u == url)
        if captured_envs is not None:
            captured_envs[name] = env
        if boom:
            raise OSError("git introuvable")
        sha = shas.get(name)
        if sha is None:
            return RunResult(argv=list(argv), returncode=1, stdout="", stderr="could not read")
        return RunResult(argv=list(argv), returncode=0, stdout=f"{sha}\trefs/heads/main\n", stderr="")
    return runner


def test_check_tools_reports_a_drifted_map(settings):
    _seed_dist(settings, "code-map", _vcs(_SHA_A))
    _seed_dist(settings, "docs-map", _vcs(_SHA_A))
    _seed_dist(settings, "front-map", _vcs(_SHA_A))
    report = tools.check_tools(settings, runner=_ls_remote_runner(
        {"code-map": _SHA_B, "docs-map": _SHA_A, "front-map": _SHA_A}))
    assert report["state"] == "differs" and report["ref"] == tools.MAP_REF
    drifted = [m for m in report["maps"] if m["state"] == "differs"]
    assert [m["name"] for m in drifted] == ["code-map"]


def test_check_tools_all_fresh_is_green(settings):
    for n in tools.MAP_REPOS:
        _seed_dist(settings, n, _vcs(_SHA_A))
    report = tools.check_tools(settings, runner=_ls_remote_runner(dict.fromkeys(tools.MAP_REPOS, _SHA_A)))
    assert report["state"] == "up-to-date"


def test_check_tools_transport_failure_degrades_without_raising(settings):
    """`git` absent / réseau coupé : la sonde rend `unknown`, elle ne lève pas et ne verdit pas. Une sonde
    qui explose hors réseau serait un check défaillant — il s'allumerait sur un état parfaitement normal."""
    for n in tools.MAP_REPOS:
        _seed_dist(settings, n, _vcs(_SHA_A))
    report = tools.check_tools(settings, runner=_ls_remote_runner({}, boom=True))
    assert report["state"] == "unknown"
    assert all(m["state"] == "unknown" and m["reason"] for m in report["maps"])


def test_check_tools_clones_anonymously(settings):
    """La sonde tape les mêmes repos que l'install : elle n'a PAS le droit d'y ajouter un credential."""
    envs: dict = {}
    for n in tools.MAP_REPOS:
        _seed_dist(settings, n, _vcs(_SHA_A))
    tools.check_tools(settings, runner=_ls_remote_runner(
        dict.fromkeys(tools.MAP_REPOS, _SHA_A), captured_envs=envs))
    for name, env in envs.items():
        added = {k: v for k, v in env.items() if os.environ.get(k) != v}
        assert set(added) <= {"GIT_TERMINAL_PROMPT"}, f"la sonde {name} ajoute à l'env : {sorted(added)}"


def test_check_tools_does_not_probe_upstream_for_a_map_it_does_not_serve(settings):
    """Rien à comparer ⇒ aucun appel réseau : la raison est déjà locale. Sans ça, une instance hors ligne
    attendrait le timeout pour trois réponses qu'elle connaissait avant de décrocher."""
    _seed_dist(settings, "code-map", _vcs(_SHA_A))                     # seule carte servie
    probed: dict = {}
    report = tools.check_tools(settings, runner=_ls_remote_runner(
        {"code-map": _SHA_A}, captured_envs=probed))
    assert set(probed) == {"code-map"}
    assert report["state"] == "unknown"                                 # les 2 autres restent non vérifiées


def test_check_exit_codes_keep_the_three_issues_distinct(settings):
    """« pas pu vérifier » (2) n'est ni « à jour » (0) ni « périmé » (1). Les confondre, c'est refaire le
    faux-vert que cette fiche répare — dans un sens ou dans l'autre."""
    assert tools._CHECK_EXITS == {"up-to-date": 0, "differs": 1, "unknown": 2}
