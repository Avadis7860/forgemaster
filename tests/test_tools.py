"""Tests de `forgemaster.tools` — provisionnement hôte-niveau de l'outillage déclaré par les bundles.

Seams PURS (chemins/PATH/plan) testés sans subprocess ; `install_tools` avec un runner INJECTÉ qui
matérialise les binaires attendus, pour prouver symlinks, idempotence et fail-loud — jamais un vrai
pip/nodeenv (lents, réseau : prouvés à la vérif install fraîche).

Depuis le 2026-08-08 les 3 cartes viennent des **wheels de l'édition** (`forgemaster/_maps`), plus d'un
`git+…@main`. Le dossier d'édition est donc INJECTÉ dans les tests (`maps_dir=`), jamais celui du paquet
installé : un test qui lirait l'édition réelle passerait ou tomberait selon que le checkout a été buildé."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forgemaster import tools
from forgemaster.config import Settings
from forgemaster.core.run import RunResult

_SHA_A = "775117a03d761abe80652a30cceae30f989be82e"      # relevé sur la VM 9311 le 2026-08-03
_SHA_B = "d04c2770000000000000000000000000000000aa"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


def _seed_edition(tmp_path: Path, shas: dict | None = None, *, omettre: str | None = None,
                  wheel_manquant: str | None = None) -> Path:
    """Un `forgemaster/_maps` crédible : 3 wheels (vides — seule leur PRÉSENCE compte pour le plan) et le
    `maps.json` que `deploy/build-wheel.sh` écrit. `omettre` retire une carte du manifeste (édition
    amputée) ; `wheel_manquant` déclare une carte dont le fichier n'existe pas (édition incohérente)."""
    shas = shas or dict.fromkeys(tools.MAP_REPOS, _SHA_A)
    d = tmp_path / "edition-maps"
    d.mkdir(parents=True, exist_ok=True)
    maps = []
    for name in tools.MAP_REPOS:
        if name == omettre:
            continue
        wheel = f"{name.replace('-', '_')}-0.1.0-py3-none-any.whl"
        if name != wheel_manquant:
            (d / wheel).write_bytes(b"PK\x03\x04")
        maps.append({"name": name, "wheel": wheel, "sha": shas[name],
                     "committed_at": "2026-08-08T00:00:00+00:00"})
    (d / tools.EDITION_MANIFEST).write_text(json.dumps({"maps": maps}), encoding="utf-8")
    return d


@pytest.fixture
def edition(tmp_path: Path) -> Path:
    return _seed_edition(tmp_path)


# -- seams PURS -------------------------------------------------------------------------------------

def test_path_layout_under_forgemaster_home(settings):
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


def test_install_plan_covers_maps_quality_and_node(settings, edition):
    plan = tools.install_plan(settings, maps_dir=edition)
    names = [s["name"] for s in plan]
    assert names == ["pip-maps", "pip-quality", "pip-nodeenv", "nodeenv"]
    pip_maps = plan[0]["argv"]
    # les 3 cartes en CHEMINS DE FICHIERS — plus aucune URL, donc plus aucun clone, donc plus aucune
    # surface où un credential pourrait entrer : la propriété est structurelle, pas gardée par un env.
    assert not any(a.startswith(("git+", "http://", "https://")) for a in pip_maps)
    for m in json.loads((edition / tools.EDITION_MANIFEST).read_text())["maps"]:
        assert str(edition / m["wheel"]) in pip_maps
    quality = next(s for s in plan if s["name"] == "pip-quality")["argv"]
    for q in tools.PY_QUALITY:
        assert q in quality
    node_step = next(s for s in plan if s["name"] == "nodeenv")
    assert node_step["argv"][0].endswith("/nodeenv") and f"--node={tools.NODE_VERSION}" in node_step["argv"]


def test_install_plan_poses_the_maps_offline(settings, edition):
    """`--no-index` : la garantie hors-ligne est dans l'argv, pas dans une intention. Sans lui, pip pourrait
    « compléter » depuis PyPI une carte qu'il jugerait insuffisante — et l'install cesserait d'être posable
    sur une machine sans réseau, sans que rien ne le dise."""
    pip_maps = next(s for s in tools.install_plan(settings, maps_dir=edition) if s["name"] == "pip-maps")
    assert "--no-index" in pip_maps["argv"]


def test_install_plan_forces_the_maps_past_the_pip_no_op_trap(settings, edition):
    """Le verrou du no-op silencieux, qui a SURVÉCU au changement de source. Vu en vrai le 2026-08-03 avec
    `git+…@main` : pip résout, prépare les métadonnées, puis SAUTE l'install à version égale — et les cartes
    sont figées à `0.1.0`, donc la version ne discrimine jamais, fichier ou pas. Retirer `--force-reinstall`
    rétablit le 🟢 sans qu'une ligne ait bougé.

    `--no-deps` n'est PAS repris : deps `[]` aujourd'hui, et le jour où une carte en gagne une, l'install
    hors-ligne doit ÉCHOUER plutôt que poser une carte amputée en rendant rc 0."""
    pip_maps = next(s for s in tools.install_plan(settings, maps_dir=edition) if s["name"] == "pip-maps")
    assert "--force-reinstall" in pip_maps["argv"]
    assert "--no-deps" not in pip_maps["argv"]


def test_install_plan_poses_the_offline_step_first(settings, edition):
    """L'ORDRE est load-bearing : les cartes sont la SEULE étape hors-ligne. Réseau coupé, elles sont posées
    et l'échec porte le nom de ce qui exigeait vraiment le réseau, au lieu de tout faire tomber d'un bloc."""
    names = [s["name"] for s in tools.install_plan(settings, maps_dir=edition)]
    assert names[0] == "pip-maps"


def test_install_plan_refuses_an_edition_that_is_absent(settings, tmp_path):
    """Aucun repli git. Quel mode d'install est actif est une question qui se RÉPOND ; une cascade
    silencieuse vers une réf mobile la pré-répondrait, et remettrait la dérive qu'on vient de fermer."""
    with pytest.raises(tools.EditionMapsError, match="ne porte pas les cartes"):
        tools.install_plan(settings, maps_dir=tmp_path / "vide")


def test_install_plan_refuses_an_amputated_edition(settings, tmp_path):
    """2 cartes sur 3 en rendant rc 0 serait le demi-provisioning que `install_tools` refuse déjà ailleurs."""
    with pytest.raises(tools.EditionMapsError, match="AMPUTÉE"):
        tools.install_plan(settings, maps_dir=_seed_edition(tmp_path, omettre="docs-map"))


def test_install_plan_refuses_a_declared_wheel_that_is_missing(settings, tmp_path):
    """Déclarée au manifeste mais absente du disque : l'incohérence se dit AVANT le premier pip, pas au
    milieu de l'install."""
    with pytest.raises(tools.EditionMapsError, match="son wheel manque"):
        tools.install_plan(settings, maps_dir=_seed_edition(tmp_path, wheel_manquant="front-map"))


def test_symlink_sources_split_venv_and_node(settings):
    srcs = tools._symlink_sources(settings)
    assert srcs["codemap"] == tools.tools_venv(settings) / "bin" / "codemap"
    assert srcs["node"] == tools.nodeenv_prefix(settings) / "bin" / "node"
    assert set(srcs) == {"codemap", "docsmap", "frontmap",
                         "ruff", "pytest", "mypy", "node", "npm", "npx"}


def test_taskmap_not_host_provisioned():
    """task-map = moteur central (`taskmap.graph`, importé en-process, vendoré au wheel), PAS une carte de
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
                else "pip-maps" if "--no-index" in argv
                else "pip-quality")
        if captured_envs is not None:
            captured_envs[step] = env
        if fail_on == step:
            return RunResult(argv=list(argv), returncode=1, stdout="", stderr="boom")
        if step == "venv":
            venv_bin.mkdir(parents=True, exist_ok=True)
        elif step == "pip-maps":
            for name in ("codemap", "docsmap", "frontmap"):
                touch(venv_bin / name)
        elif step == "pip-quality":
            for name in ("ruff", "pytest", "mypy"):
                touch(venv_bin / name)
        elif step == "pip-nodeenv":
            touch(venv_bin / "nodeenv")
        elif step == "nodeenv":
            for name in ("node", "npm", "npx"):
                touch(node_bin / name)
        return RunResult(argv=list(argv), returncode=0, stdout="ok", stderr="")

    return runner


def test_install_tools_happy_path_exposes_all_bins(settings, edition):
    report = tools.install_tools(settings, runner=_materializing_runner(settings), maps_dir=edition)
    assert report["ok"] is True
    bin_dir = tools.tools_bin(settings)
    for name in ("codemap", "docsmap", "frontmap", "ruff", "pytest", "mypy", "node", "npm", "npx"):
        link = bin_dir / name
        assert link.is_symlink() and link.resolve().exists()            # exposé ET pointe une source réelle
    assert set(report["symlinks"]) == set(tools._symlink_sources(settings))


def test_install_tools_is_idempotent(settings, edition):
    r1 = tools.install_tools(settings, runner=_materializing_runner(settings), maps_dir=edition)
    # 2e run : remplace les symlinks existants sans erreur
    r2 = tools.install_tools(settings, runner=_materializing_runner(settings), maps_dir=edition)
    assert r1["ok"] and r2["ok"]
    assert (tools.tools_bin(settings) / "codemap").is_symlink()


def test_install_tools_fail_loud_aborts_before_symlink(settings, edition):
    report = tools.install_tools(settings, runner=_materializing_runner(settings, fail_on="pip-maps"),
                                 maps_dir=edition)
    assert report["ok"] is False
    assert "pip-maps" in report["error"]
    assert not (tools.tools_bin(settings) / "codemap").exists()   # pas de symlink sur un demi-provisioning
    failed = [s for s in report["steps"] if not s["ok"]]
    assert failed and failed[0]["name"] == "pip-maps"


def test_install_tools_missing_binary_after_green_install_is_loud(settings, edition):
    """Étapes vertes mais une source absente (install incohérente) → fail-loud, pas de faux-vert."""
    def runner(argv, *, env=None, timeout=None):
        if "venv" in argv and "-m" in argv:
            (tools.tools_venv(settings) / "bin").mkdir(parents=True, exist_ok=True)
        return RunResult(argv=list(argv), returncode=0, stdout="ok", stderr="")   # ne matérialise AUCUN bin
    report = tools.install_tools(settings, runner=runner, maps_dir=edition)
    assert report["ok"] is False and "absent après install" in report["error"]


def test_install_tools_refuses_before_touching_anything_when_the_edition_is_absent(settings, tmp_path):
    """Une édition non posable est un refus AVANT le premier pip — et le venv n'a pas encore été peuplé.
    C'est la même règle que partout ici : jamais un demi-provisioning."""
    report = tools.install_tools(settings, runner=_materializing_runner(settings),
                                 maps_dir=tmp_path / "vide")
    assert report["ok"] is False and "ne porte pas les cartes" in report["error"]
    assert not (tools.tools_bin(settings) / "codemap").exists()
    assert [s["name"] for s in report["steps"]] == ["venv"]      # le venv, et rien d'autre


def test_install_tools_adds_nothing_to_the_environment(settings, edition):
    """L'install n'AJOUTE rien à l'env ambiant — donc aucun credential.

    Le `GIT_TERMINAL_PROMPT=0` d'avant gardait les clones git de pip ; il n'y a plus de clone ici, donc
    plus rien à garder (le seam vit toujours chez `mcp.local`, qui clone). L'assertion porte sur le
    **delta** avec l'ambiant, pas sur l'absence de tout token dans l'env : ce que l'utilisateur a dans son
    shell reste à lui. Ce qui est interdit, c'est que le forgemaster compose quoi que ce soit."""
    envs: dict = {}
    tools.install_tools(settings, runner=_materializing_runner(settings, captured_envs=envs),
                        maps_dir=edition)
    for name, env in envs.items():
        added = {k: v for k, v in env.items() if os.environ.get(k) != v}
        assert not added, f"l'étape {name} ajoute à l'env : {sorted(added)}"


def test_install_tools_has_no_url_left_to_authenticate(settings, edition):
    """La propriété « aucun credential n'entre ici » n'est plus tenue par un env de précaution mais par
    l'ABSENCE DU CHEMIN : aucune étape ne porte d'URL, donc il n'y a plus rien à authentifier."""
    for step in tools.install_plan(settings, maps_dir=edition):
        urls = [a for a in step["argv"] if a.startswith(("git+", "http://", "https://"))]
        assert not urls, f"l'étape {step['name']} porte une URL : {urls}"


def test_install_tools_takes_no_credential_argument(settings):
    """La signature ne porte plus `token`/`token_ref` : le chemin d'auth est RETIRÉ, pas rendu optionnel."""
    import inspect
    params = inspect.signature(tools.install_tools).parameters
    assert "token" not in params and "token_ref" not in params


# -- provenance des cartes SERVIES (lecture locale : tampon d'édition, puis PEP 610 ; zéro réseau) -----

_PKG = {"code-map": "codemap", "docs-map": "docsmap", "front-map": "frontmap"}


def _seed_dist(settings, name: str, payload, *, version: str = "0.1.0", stamp: str | None = None,
               record_pkg: str | None = None) -> Path:
    """Matérialise `<name>-<version>.dist-info` dans le site-packages du venv d'outils. `payload` : un dict
    (sérialisé), une str (écrite telle quelle — JSON invalide), ou None (aucun `direct_url.json`).
    `stamp` pose le tampon `_vendored_from.txt` DANS le paquet et le déclare au `RECORD`, exactement comme
    un `pip install` d'un wheel de l'édition. `record_pkg` force le nom de paquet écrit au RECORD (pour
    prouver qu'on le LIT au lieu de le deviner depuis le nom de distribution)."""
    sp = tools.tools_venv(settings) / "lib" / "python3.12" / "site-packages"
    d = sp / f"{name.replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        body = json.dumps(payload) if isinstance(payload, dict) else payload
        (d / "direct_url.json").write_text(body, encoding="utf-8")
    if stamp is not None:
        pkg = record_pkg or _PKG[name]
        (sp / pkg).mkdir(parents=True, exist_ok=True)
        (sp / pkg / tools.VENDORED_FROM).write_text(f"{stamp}\n", encoding="utf-8")
        (d / "RECORD").write_text(f"{pkg}/__init__.py,sha256=x,12\n"
                                  f"{pkg}/{tools.VENDORED_FROM},sha256=y,41\n", encoding="utf-8")
    return d


def _vcs(sha: str, ref: str = "main") -> dict:
    return {"url": "https://github.com/Avadis7860/code-map.git",
            "vcs_info": {"commit_id": sha, "requested_revision": ref, "vcs": "git"}}


def test_maps_provenance_reads_the_edition_stamp(settings):
    """Le mode CANONIQUE depuis le 2026-08-08 : la carte vient d'un wheel de l'édition, et son SHA vit dans
    le tampon posé DANS le paquet. Un wheel n'a pas de `vcs_info` — s'en tenir à PEP 610 rendrait `sha=None`
    sur exactement le mode qu'on vient de rendre canonique."""
    _seed_dist(settings, "code-map", {"url": "file:///tmp/x.whl", "archive_info": {}}, stamp=_SHA_A)
    entry = next(m for m in tools.maps_provenance(settings) if m["name"] == "code-map")
    assert entry["sha"] == _SHA_A and entry["source"] == "edition" and entry["reason"] is None


def test_maps_provenance_finds_the_stamp_through_the_record_not_by_guessing(settings):
    """`code-map` → `codemap` est une CONVENTION, pas une règle : PEP 503 normalise le nom de DISTRIBUTION
    et ne dit rien du nom d'IMPORT. Le tampon se localise par le `RECORD`, exigé par le format wheel."""
    _seed_dist(settings, "docs-map", None, stamp=_SHA_B, record_pkg="un_nom_qui_ne_se_devine_pas")
    entry = next(m for m in tools.maps_provenance(settings) if m["name"] == "docs-map")
    assert entry["sha"] == _SHA_B and entry["source"] == "edition"


def test_maps_provenance_rejects_a_stamp_that_is_not_a_sha(settings):
    """Un tampon corrompu ne devient pas une identité : on retombe sur la cascade PEP 610, et si elle n'a
    rien non plus, `sha=None` AVEC sa raison. Un SHA faux retire le doute qui déclenche la vérification."""
    _seed_dist(settings, "front-map", None, stamp="pas-un-sha")
    entry = next(m for m in tools.maps_provenance(settings) if m["name"] == "front-map")
    assert entry["sha"] is None and entry["reason"]


def test_maps_provenance_reads_the_served_commit(settings):
    """Le mode HISTORIQUE, encore vivant sur toute instance provisionnée avant le 2026-08-08 : pip a posé
    `direct_url.json` à l'install git, on LIT le `commit_id` résolu. C'est ce qui distingue les deux modes —
    et le distinguer est précisément ce que la phase 4·3 rendra visible."""
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
    """Un forgemaster en checkout dev n'a pas de `tools/venv` : ce n'est PAS une erreur. On rend les 3 entrées
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


# -- conformité à l'édition : seams purs --------------------------------------------------------------

def test_read_edition_returns_the_three_maps_in_order(settings, edition):
    declared = tools.read_edition(edition)
    assert [m["name"] for m in declared] == list(tools.MAP_REPOS)
    assert all(m["sha"] == _SHA_A for m in declared)


def test_read_edition_refuses_an_unreadable_manifest(settings, tmp_path):
    d = tmp_path / "ed"
    d.mkdir()
    (d / tools.EDITION_MANIFEST).write_text("{pas du json", encoding="utf-8")
    with pytest.raises(tools.EditionMapsError, match="illisible"):
        tools.read_edition(d)


def test_edition_maps_dir_sits_inside_the_installed_package(settings):
    """Le dossier voyage DANS le wheel (`forgemaster/_maps`), comme `_verify_runner` : `provision-ct.sh`
    n'a rien à câbler, l'artefact est auto-contenu."""
    import forgemaster
    assert tools.edition_maps_dir() == Path(forgemaster.__file__).resolve().parent / "_maps"


def test_compare_up_to_date(settings):
    served = [{"name": "code-map", "sha": _SHA_A, "reason": None}]
    assert tools.compare(served, {"code-map": _SHA_A})[0]["state"] == "up-to-date"


def test_compare_differs_writes_both_shas(settings):
    """Le verdict porte les DEUX SHA : c'est ce qui rend l'écart actionnable sans ssh sur la machine."""
    served = [{"name": "code-map", "sha": _SHA_A, "reason": None}]
    e = tools.compare(served, {"code-map": _SHA_B})[0]
    assert e["state"] == "differs" and e["served"] == _SHA_A and e["edition"] == _SHA_B


def test_compare_never_invents_a_commit_count(settings):
    """Deux SHA ne se soustraient pas sans l'historique. On dit LESQUELLES diffèrent, jamais « de N
    commits ». Un chiffre faux retirerait le doute qui doit déclencher la vérification."""
    served = [{"name": "code-map", "sha": _SHA_A, "reason": None}]
    e = tools.compare(served, {"code-map": _SHA_B})[0]
    assert not {"behind_by", "behind", "count", "commits"} & set(e)


def test_compare_a_silent_edition_is_unknown_not_up_to_date(settings):
    """Le faux-vert qu'on refuse : édition muette sur cette carte ⇒ « pas pu comparer », JAMAIS
    « conforme »."""
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


# -- conformité à l'édition : la sonde, désormais LOCALE ----------------------------------------------

def test_check_tools_reports_a_map_that_is_not_the_editions(settings, edition):
    """La question a changé avec l'épinglage. Ce n'est plus « suis-je en retard sur upstream ? » — c'est la
    question du WHEEL — mais « mes cartes sont-elles celles de mon édition ? », qui se pose exactement quand
    une instance a monté d'édition sans reposer son outillage, et qui n'avait aucune réponse."""
    _seed_dist(settings, "code-map", None, stamp=_SHA_B)
    _seed_dist(settings, "docs-map", None, stamp=_SHA_A)
    _seed_dist(settings, "front-map", None, stamp=_SHA_A)
    report = tools.check_tools(settings, maps_dir=edition)
    assert report["state"] == "differs" and report["edition_dir"] == str(edition)
    drifted = [m for m in report["maps"] if m["state"] == "differs"]
    assert [m["name"] for m in drifted] == ["code-map"]
    assert drifted[0]["served"] == _SHA_B and drifted[0]["edition"] == _SHA_A


def test_check_tools_conformant_is_green(settings, edition):
    for n in tools.MAP_REPOS:
        _seed_dist(settings, n, None, stamp=_SHA_A)
    assert tools.check_tools(settings, maps_dir=edition)["state"] == "up-to-date"


def test_check_tools_takes_no_runner_and_makes_no_subprocess(settings, edition):
    """Elle ne prend plus de runner : il n'y a plus rien à exécuter. Une sonde purement locale ne peut plus
    échouer parce que le réseau est coupé — le mode d'échec le plus fréquent de l'ancienne a disparu."""
    import inspect
    assert "runner" not in inspect.signature(tools.check_tools).parameters


def test_check_tools_without_an_edition_is_unknown_not_green(settings, tmp_path):
    """Checkout dev / wheel dégradé : l'édition ne déclare rien. C'est `unknown` AVEC sa raison — jamais
    « conforme » (il n'y a rien à quoi se conformer), jamais une exception depuis une sonde."""
    for n in tools.MAP_REPOS:
        _seed_dist(settings, n, None, stamp=_SHA_A)
    report = tools.check_tools(settings, maps_dir=tmp_path / "vide")
    assert report["state"] == "unknown" and report["edition_dir"] is None and report["reason"]
    assert all(m["state"] == "unknown" and m["reason"] for m in report["maps"])


def test_check_tools_a_map_not_installed_stays_unknown(settings, edition):
    """Rien de servi ⇒ rien à comparer : la raison est locale et déjà connue. Elle remonte telle quelle."""
    _seed_dist(settings, "code-map", None, stamp=_SHA_A)                # seule carte servie
    report = tools.check_tools(settings, maps_dir=edition)
    assert report["state"] == "unknown"                                 # les 2 autres restent non vérifiées
    assert [m["state"] for m in report["maps"]] == ["up-to-date", "unknown", "unknown"]


def test_check_exit_codes_keep_the_three_issues_distinct(settings):
    """« pas pu vérifier » (2) n'est ni « à jour » (0) ni « périmé » (1). Les confondre, c'est refaire le
    faux-vert que cette fiche répare — dans un sens ou dans l'autre."""
    assert tools._CHECK_EXITS == {"up-to-date": 0, "differs": 1, "unknown": 2}


def test_read_edition_refuses_an_entry_missing_a_field(settings, tmp_path):
    """Un `KeyError` qui s'échapperait d'ici remonterait BRUT à `check_tools`, dont le contrat est de ne
    JAMAIS lever — la sonde tomberait au lieu de rendre `unknown`. Le manifeste est donc décodé entièrement,
    champs compris, dans le seul endroit qui sait le refuser."""
    d = tmp_path / "ed"
    d.mkdir()
    (d / tools.EDITION_MANIFEST).write_text(json.dumps({"maps": [{"name": "code-map"}]}), encoding="utf-8")
    with pytest.raises(tools.EditionMapsError, match="illisible"):
        tools.read_edition(d)
    assert tools.check_tools(settings, maps_dir=d)["state"] == "unknown"      # la sonde, elle, tient
