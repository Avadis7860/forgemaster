"""Tests de `forgemaster.mcp.local` — le serveur forgemaster-catalogs co-installé, et la topologie déclarée.

Deux moitiés, comme le module : les **seams purs** (chemins, URL, argv, rendu de fichiers) testés sans
subprocess, et `install` avec un **runner injecté** qui matérialise ce que pip aurait posé — jamais un vrai
clone git (lent, réseau, et le dépôt est encore privé).

Le test qui compte le plus est celui de la topologie **`remote`** : c'est la lecture ROUGE qui donne son
sens à `co-installed`. Une sonde qui ne saurait dire que « co-installé » ne prouverait rien — elle ne
distinguerait pas une instance qui fait tourner son serveur d'une instance qui n'en fait rien.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from forgemaster import build_provenance
from forgemaster.config import Settings
from forgemaster.core.run import RunResult
from forgemaster.mcp import local
from forgemaster.provision import mcp as wiring

_SHA = "0d481d3c2795a35549515b67acb3abd6b314e31b"
_REMOTE = "http://192.168.0.153:8080/mcp"       # un endpoint d'ailleurs : la topologie `remote`


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


@pytest.fixture(autouse=True)
def _isolated_wiring_env():
    """Aucun câblage hérité de l'environnement du développeur : chaque test DIT ce qu'il câble. Sans ça,
    une machine de dev câblée sur notre CT ferait passer au vert un test de topologie `none`.

    **Restauration à la main, et pas `monkeypatch.delenv`** — piège vérifié : `delenv(raising=False)` sur une
    clé ABSENTE n'enregistre rien à restaurer, si bien que ce que le code de production écrit ensuite
    (`wire(live_env=True)` pose les deux clés dans `os.environ`) SURVIT au test. Le symptôme était à distance
    et muet : `test_tooling_preflight` tombait en rouge parce que `forgemaster doctor` voyait un câblage MCP
    laissé par ce module-ci. On restaure donc l'instantané, y compris en RETIRANT les clés qu'un test a
    fait naître."""
    keys = (wiring.ENV_MCP_ENDPOINT, wiring.ENV_MCP_JWT_SECRET_REF)
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _seed_server_dist(settings: Settings, *, sha: str | None = _SHA) -> Path:
    """Matérialise ce que `pip install git+…@<sha>` aurait posé : un `.dist-info` avec son
    `direct_url.json` (PEP 610). C'est la SEULE trace sur laquelle la provenance s'appuie — aucun tampon
    n'est écrit par nous."""
    sp = local.mcp_venv(settings) / "lib" / "python3.12" / "site-packages"
    dist = sp / "forgemaster_catalogs-0.1.0.dist-info"
    dist.mkdir(parents=True, exist_ok=True)   # ré-install : pip écrase, la fixture aussi
    payload: dict = {"url": local.SERVER_REPO}
    payload |= ({"vcs_info": {"commit_id": sha, "requested_revision": sha, "vcs": "git"}} if sha
                else {"dir_info": {"editable": True}})
    (dist / "direct_url.json").write_text(json.dumps(payload), encoding="utf-8")
    return dist


# -- seams PURS ---------------------------------------------------------------------------------------

def test_three_venvs_never_collide(settings):
    """Le serveur a SON venv, distinct de celui du forgemaster et de celui des outils : trois cycles de
    vie."""
    from forgemaster import tools
    assert local.mcp_root(settings) == settings.home / "mcp"
    assert local.mcp_venv(settings) == settings.home / "mcp" / "venv"
    assert local.mcp_venv(settings) != tools.tools_venv(settings)
    assert local.env_file(settings) == settings.home / "mcp" / "forgemaster-catalogs.env"


@pytest.mark.parametrize("endpoint,expected", [
    ("http://127.0.0.1:8080/mcp", True),
    ("http://127.0.0.53:8080/mcp", True),           # toute la plage 127.0.0.0/8, pas la seule .1
    ("http://localhost:8080/mcp", True),
    ("http://[::1]:8080/mcp", True),
    ("http://192.168.0.153:8080/mcp", False),
    ("http://mcp.example.org/mcp", False),          # nom d'hôte : AUCUN DNS résolu (sonde hors réseau)
    ("http://0.0.0.0:8080/mcp", False),             # adresse de BIND, jamais de destination
    ("", False),
    (None, False),
])
def test_is_loopback_is_pure_and_resolves_no_dns(endpoint, expected):
    assert local.is_loopback(endpoint) is expected


def test_install_plan_forces_the_server_past_the_pip_git_sha_trap(settings):
    """`forgemaster-catalogs` est figé à 0.1.0 : `--upgrade` seul saute l'install en rendant rc 0. La 2ᵈᵉ
    passe force le CODE à la réf demandée sans retoucher aux deps. Retirer ce test, c'est rouvrir le
    faux-vert dans lequel un bump de `SERVER_REF` ne changerait rien en répondant « 🟢 »."""
    plan = local.install_plan(settings)
    assert [s["name"] for s in plan] == ["pip-server", "pip-server-pin"]
    assert plan[0]["argv"][-3:] == ["install", "--upgrade", f"git+{local.SERVER_REPO}@{local.SERVER_REF}"]
    assert "--force-reinstall" in plan[1]["argv"] and "--no-deps" in plan[1]["argv"]


def test_install_plan_pins_a_full_sha_not_a_moving_ref(settings):
    """§3 de la décision d'édition : la pièce co-installée monte AVEC l'édition. Une réf mobile (`main`)
    la ferait dériver seule — c'est exactement ce qui a rendu `forgemaster toolchain check` nécessaire côté
    cartes."""
    assert len(local.SERVER_REF) == 40 and all(c in "0123456789abcdef" for c in local.SERVER_REF)
    assert local.SERVER_REF in str(local.install_plan(settings)[0]["argv"])


def test_rendered_env_binds_loopback_and_names_the_data_root(tmp_path):
    """Un serveur co-installé ne s'expose pas au LAN, et sa racine de donnée est écrite EXPLICITE (le
    résolveur par remontée du serveur cherche un `catalogs/` qu'un corpus typé n'a pas)."""
    env = local.render_env(port=8080, data_root=tmp_path / "corpus", secret="s" * 40)
    assert f"VAULT_MCP_HOST={local.LOOPBACK_HOST}" in env
    assert "0.0.0.0" not in env
    assert f"DATA_ROOT={tmp_path / 'corpus'}" in env


def test_rendered_env_reuses_the_wiring_jwt_contract(tmp_path):
    """iss/aud viennent de `provision.mcp`, pas d'une copie : deux constantes jumelles qui dériveraient
    produiraient un serveur refusant les jetons de son propre forgemaster, avec un 401 muet sur la cause."""
    env = local.render_env(port=8080, data_root=tmp_path, secret="s" * 40)
    assert f"VAULT_MCP_JWT_ISSUER={wiring.MCP_ISSUER}" in env
    assert f"VAULT_MCP_JWT_AUDIENCE={wiring.MCP_AUDIENCE}" in env


def test_rendered_unit_points_at_the_dedicated_venv_and_data_root(settings, tmp_path):
    unit = local.render_unit(settings, data_root=tmp_path / "corpus", scope="user")
    assert f"WorkingDirectory={tmp_path / 'corpus'}" in unit
    assert f"ExecStart={local.mcp_venv(settings) / 'bin' / 'forgemaster-catalogs'} serve" in unit
    assert f"EnvironmentFile={local.env_file(settings)}" in unit
    assert "User=" not in unit                                   # portée user : pas d'identité épinglée
    assert "User=" in local.render_unit(settings, data_root=tmp_path, scope="system")


def test_rendered_unit_never_carries_the_secret(settings, tmp_path):
    """L'unité est lisible par tout le monde ; le secret vit dans l'EnvironmentFile en 600. Les confondre
    publierait le secret HS256 sous `~/.config/systemd`."""
    assert "JWT_SECRET" not in local.render_unit(settings, data_root=tmp_path)


# -- topologie : les trois états, dont le ROUGE qui donne son sens au vert ------------------------------

def test_topology_without_an_endpoint_is_a_normal_state(settings):
    """`none` n'est pas une panne : une install sans corpus n'a pas d'instance à interroger. Elle le DIT."""
    t = local.topology(settings)
    assert t["topology"] == "none" and t["sha"] is None and t["endpoint"] is None
    assert "aucun endpoint" in t["reason"]


def test_topology_remote_refuses_to_guess_a_sha(settings, monkeypatch):
    """LE ROUGE D'ABORD. Câblée sur un serveur d'ailleurs, l'instance rend `remote` et `sha: null` AVEC son
    motif — elle ne peut pas lire localement le build d'une machine qui n'est pas la sienne, et un SHA faux
    coûterait plus cher qu'un SHA manquant : il retirerait le doute qui déclenche la vérification."""
    monkeypatch.setenv(wiring.ENV_MCP_ENDPOINT, _REMOTE)
    t = local.topology(settings)
    assert t["topology"] == "remote"
    assert t["sha"] is None and t["endpoint"] == _REMOTE
    assert "ne se lit pas localement" in t["reason"]


def test_topology_co_installed_serves_the_local_sha(settings, monkeypatch):
    """Le vert : un serveur posé ici ET un endpoint en loopback. Seul cas qui porte un SHA, parce que seul
    cas où le binaire servi est sur ce disque."""
    _seed_server_dist(settings)
    monkeypatch.setenv(wiring.ENV_MCP_ENDPOINT, local.endpoint_url(8080))
    t = local.topology(settings)
    assert t["topology"] == "co-installed"
    assert t["sha"] == _SHA
    assert t["endpoint"] == "http://127.0.0.1:8080/mcp"


def test_topology_says_remote_when_a_local_server_is_not_the_one_consumed(settings, monkeypatch):
    """Le cas tordu : serveur installé ici, mais l'instance consomme ailleurs. La topologie décrit ce qu'on
    CONSOMME (`remote`) et signale le serveur local inutilisé — sans quoi un opérateur lirait « remote » sur
    une machine qui fait tourner un serveur, et croirait à un bug."""
    _seed_server_dist(settings)
    monkeypatch.setenv(wiring.ENV_MCP_ENDPOINT, _REMOTE)
    t = local.topology(settings)
    assert t["topology"] == "remote"
    assert "ne le consomme pas" in t["reason"]


def test_topology_installed_but_unwired_is_none_not_co_installed(settings):
    """Serveur posé, aucun endpoint : l'instance ne consomme RIEN. Déduire `co-installed` de la seule
    présence du venv mentirait — elle ne dirait pas que le câblage manque."""
    _seed_server_dist(settings)
    assert local.topology(settings)["topology"] == "none"


def test_topology_never_raises(settings, monkeypatch):
    """Servie depuis `GET /api/version` : une sonde qui lève transforme une provenance illisible en 500."""
    monkeypatch.setenv(wiring.ENV_MCP_ENDPOINT, local.endpoint_url())
    monkeypatch.setattr(local, "server_provenance", lambda s: (_ for _ in ()).throw(OSError("boom")))
    assert local.topology(settings)["topology"] == "unknown"


def test_api_version_carries_the_topology(settings, monkeypatch):
    """Le champ est servi, pas seulement calculable : c'est `/api/version` qui doit répondre « laquelle des
    deux topologies suis-je », et c'est là que la décision d'édition (§4) l'exige."""
    monkeypatch.setenv(wiring.ENV_MCP_ENDPOINT, _REMOTE)
    prov = build_provenance.provenance(settings)
    assert prov["mcp"]["topology"] == "remote"
    assert set(prov["mcp"]) == {"topology", "sha", "endpoint", "reason"}


def test_api_version_degrades_honestly_when_the_probe_dies(settings, monkeypatch):
    import forgemaster.mcp.local as mod
    monkeypatch.setattr(mod, "topology", lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    assert build_provenance.provenance(settings)["mcp"]["topology"] == "unknown"


# -- install (runner injecté) --------------------------------------------------------------------------

def _runner_ok(settings: Settings):
    """Runner qui matérialise ce que pip aurait posé, et JOURNALISE les argv vus — c'est ce journal qui
    permet d'asserter qu'aucun secret n'a transité par une ligne de commande."""
    seen: list[list[str]] = []

    def runner(argv, *, env=None, timeout=None):
        seen.append(list(argv))
        if argv[1:3] == ["-m", "venv"]:
            (Path(argv[3]) / "bin").mkdir(parents=True, exist_ok=True)
        if "--force-reinstall" in argv:
            _seed_server_dist(settings)
        return RunResult(argv=list(argv), returncode=0, stdout="", stderr="")

    runner.seen = seen                                          # type: ignore[attr-defined]
    return runner


def test_install_refuses_without_an_existing_data_root(settings, tmp_path):
    """Le refus EST la fonctionnalité : un serveur démarré sur une racine absente répond 200 sur un
    corpus vide, et cette réussite apparente est pire qu'un échec — c'est le cap silencieux que
    l'invariant interdit."""
    with pytest.raises(local.McpInstallError) as exc:
        local.install(settings, data_root=tmp_path / "nexistepas", runner=_runner_ok(settings))
    assert "racine de donnée introuvable" in str(exc.value)
    assert not local.unit_path("user").exists()                 # rien de posé sur un refus


def test_install_writes_unit_env_and_wires_the_forgemaster(settings, tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setattr(local, "unit_path", lambda scope="user": tmp_path / "units" / "fmc.service")
    report = local.install(settings, data_root=corpus, port=8099, runner=_runner_ok(settings))

    assert report["ok"] and report["endpoint"] == "http://127.0.0.1:8099/mcp"
    assert report["sha"] == _SHA                                # lu du disque, pas déclaré
    assert Path(report["unit"]).exists() and Path(report["env_file"]).exists()
    # le forgemaster se câble sur SON serveur, sans restart (live_env) — et la topologie bascule au vert
    assert os.environ[wiring.ENV_MCP_ENDPOINT] == "http://127.0.0.1:8099/mcp"
    assert local.topology(settings)["topology"] == "co-installed"


def test_install_leaves_the_env_file_owner_readable_only(settings, tmp_path, monkeypatch):
    """Il porte le secret HS256 en clair — le serveur n'a pas de coffre à interroger."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setattr(local, "unit_path", lambda scope="user": tmp_path / "units" / "fmc.service")
    report = local.install(settings, data_root=corpus, runner=_runner_ok(settings))
    mode = stat.S_IMODE(Path(report["env_file"]).stat().st_mode)
    assert mode == 0o600, f"mode {mode:o}"


def test_install_never_puts_the_secret_in_a_command_line(settings, tmp_path, monkeypatch):
    """Le secret est généré ici et écrit dans un fichier 600 ; il ne doit apparaître dans AUCUN argv (un
    argv est lisible par tout le monde dans `ps`)."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setattr(local, "unit_path", lambda scope="user": tmp_path / "units" / "fmc.service")
    runner = _runner_ok(settings)
    report = local.install(settings, data_root=corpus, runner=runner)
    secret = next(line.split("=", 1)[1]
                  for line in Path(report["env_file"]).read_text().splitlines()
                  if line.startswith("VAULT_MCP_JWT_SECRET="))
    assert len(secret) >= 32
    assert all(secret not in " ".join(argv) for argv in runner.seen)   # type: ignore[attr-defined]


def test_install_is_idempotent_on_the_secret(settings, tmp_path, monkeypatch):
    """Ré-exécuter une commande annoncée idempotente ne doit pas invalider les jetons du serveur qui
    tourne : le secret déjà câblé est RÉUTILISÉ, pas régénéré."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setattr(local, "unit_path", lambda scope="user": tmp_path / "units" / "fmc.service")
    first = local.install(settings, data_root=corpus, runner=_runner_ok(settings))
    secret_1 = Path(first["env_file"]).read_text()
    second = local.install(settings, data_root=corpus, runner=_runner_ok(settings))
    assert second["ok"]
    assert Path(second["env_file"]).read_text() == secret_1


def test_install_aborts_on_a_red_pip_step_without_posing_a_unit(settings, tmp_path, monkeypatch):
    """Jamais un demi-provisioning : une étape rouge abandonne AVANT d'écrire l'unité, pour qu'aucun
    systemctl ne puisse démarrer un serveur qui n'a pas été installé."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    unit = tmp_path / "units" / "fmc.service"
    monkeypatch.setattr(local, "unit_path", lambda scope="user": unit)

    def red(argv, *, env=None, timeout=None):
        if argv[1:3] == ["-m", "venv"]:
            (Path(argv[3]) / "bin").mkdir(parents=True, exist_ok=True)
            return RunResult(argv=list(argv), returncode=0, stdout="", stderr="")
        return RunResult(argv=list(argv), returncode=1, stdout="", stderr="no such ref")

    report = local.install(settings, data_root=corpus, runner=red)
    assert not report["ok"] and "pip-server" in str(report["error"])
    assert not unit.exists()


def test_install_clone_is_anonymous_unless_a_token_is_named(settings, tmp_path, monkeypatch):
    """Aucun credential ne peut se glisser dans l'env d'un enfant git par défaut — même garde que les 3
    cartes. Le dépôt est encore privé (P6.4 le publie) : `--token-file` est une voie EXPLICITE, jamais
    un défaut."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setattr(local, "unit_path", lambda scope="user": tmp_path / "units" / "fmc.service")
    seen_env: list[dict] = []

    def runner(argv, *, env=None, timeout=None):
        seen_env.append(dict(env or {}))
        if argv[1:3] == ["-m", "venv"]:
            (Path(argv[3]) / "bin").mkdir(parents=True, exist_ok=True)
        if "--force-reinstall" in argv:
            _seed_server_dist(settings)
        return RunResult(argv=list(argv), returncode=0, stdout="", stderr="")

    local.install(settings, data_root=corpus, runner=runner)
    pip_env = seen_env[-1]
    assert pip_env.get("GIT_TERMINAL_PROMPT") == "0"            # jamais de prompt qui fait pendre pip
    assert not any("insteadOf" in str(v) for v in pip_env.values())
