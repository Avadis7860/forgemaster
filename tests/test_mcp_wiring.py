"""Tests du câblage MCP (P6) : mint JWT stdlib, rendu du `.mcp.json`, injection au dispatch, et surtout
l'**invariant de sécurité** — le `.mcp.json` porteur du Bearer est gitignoré, donc le `git add -A` de la forge
ne peut jamais le committer. Runner injecté (aucun vrai `claude`), coffre fichier réel, worktree bare réel."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from forgemaster.config import Settings
from forgemaster.core import run
from forgemaster.db import store
from forgemaster.dispatch import worker, worktree
from forgemaster.git.internal import InternalGit
from forgemaster.projects import registry
from forgemaster.provision import mcp
from forgemaster.roadmap import model
from forgemaster.secrets import build_store
from forgemaster.secrets.jwt import mint_hs256, verify_hs256

_SECRET = "k" * 40  # ≥32c : exigence HS256
_ENDPOINT = "http://mcp.example:8080/mcp"   # hôte FICTIF : il n'y a plus d'endpoint par défaut à hériter


@pytest.fixture
def wired_endpoint(monkeypatch: pytest.MonkeyPatch) -> str:
    """Simule une install dont le MCP est **câblé** — l'endpoint vient de l'env, jamais d'un défaut en dur.
    Volontairement NON autouse : un test qui a besoin d'une cible MCP doit le DIRE, exactement comme un
    utilisateur doit la câbler. Un fixture autouse redonnerait aux tests le défaut qu'on vient de retirer du
    produit, et masquerait la régression qu'il est censé attraper."""
    monkeypatch.setenv(mcp.ENV_MCP_ENDPOINT, _ENDPOINT)
    return _ENDPOINT


def _settings(tmp_path: Path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


def _seed_project(conn, settings, *, project="proj", feature="feat", task="t") -> None:
    registry.create_project(conn, settings, slug=project)
    model.add_feature(conn, project_slug=project, slug=feature)
    model.add_task(conn, feature_ref=f"{project}/{feature}", slug=task)


# -- mint JWT stdlib --------------------------------------------------------------------------------

def test_mint_and_verify_roundtrip():
    tok = mint_hs256("forgemaster:void-runner", _SECRET, audience="vault-catalogs", issuer="vault-mcp",
                     ttl_seconds=60)
    claims = verify_hs256(tok, _SECRET, audience="vault-catalogs", issuer="vault-mcp")
    assert claims is not None
    assert claims["sub"] == "forgemaster:void-runner"
    assert claims["aud"] == "vault-catalogs" and claims["iss"] == "vault-mcp"
    # mauvais secret / mauvaise audience → refusé (contrôle du contrat serveur)
    assert verify_hs256(tok, "x" * 40, audience="vault-catalogs") is None
    assert verify_hs256(tok, _SECRET, audience="autre") is None


def test_mint_rejects_short_secret():
    with pytest.raises(ValueError):
        mint_hs256("s", "trop-court", audience="vault-catalogs")


# -- P4 : cycle de vie du token (token_exp / worktree_token / check_lifecycle) ----------------------

def test_token_exp_reads_exp_and_none_on_malformed():
    tok = mint_hs256("forgemaster:x", _SECRET, audience="vault-catalogs", ttl_seconds=3600)
    exp = mcp.token_exp(tok)
    assert exp is not None and exp > int(time.time())
    assert mcp.token_exp("pas-un-jwt") is None and mcp.token_exp("a.b") is None    # malformé → None


def test_worktree_token_extracts_bearer(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".mcp.json").write_text(json.dumps(mcp.render_mcp_config("TOK123", endpoint=_ENDPOINT)),
                                  encoding="utf-8")
    assert mcp.worktree_token(wt) == "TOK123"
    assert mcp.worktree_token(tmp_path / "vide") is None       # .mcp.json absent → None


def test_check_lifecycle_unconfigured_is_honest(tmp_path: Path):
    st = mcp.check_lifecycle(_settings(tmp_path), now=1000, secret_ref="")
    assert st["configured"] is False and st["healthy"] is True  # install public sans corpus privé


def test_check_lifecycle_bad_secret_is_unhealthy(tmp_path: Path):
    st = mcp.check_lifecycle(_settings(tmp_path), now=1000, secret_ref="ref", resolver=lambda _r: "court")
    assert st["configured"] and not st["healthy"] and "secret" in st["reason"]


def test_check_lifecycle_healthy_when_no_stale_worktree(tmp_path: Path, wired_endpoint: str):
    st = mcp.check_lifecycle(_settings(tmp_path), now=int(time.time()),
                             secret_ref="ref", resolver=lambda _r: _SECRET)
    assert st["configured"] and st["healthy"] and st["exp"] > int(time.time()) and st["stale"] == []


def test_check_lifecycle_without_endpoint_is_unhealthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Câblage à moitié fait : la ref de secret est posée, mais aucune cible. Sans défaut en dur,
    `inject_mcp_config` no-ope **silencieusement** dans cet état — le doctor doit le dire, sinon le worker
    part sans MCP sans un mot (le trou qu'ouvrirait le retrait du défaut si personne ne le surveillait)."""
    monkeypatch.delenv(mcp.ENV_MCP_ENDPOINT, raising=False)
    st = mcp.check_lifecycle(_settings(tmp_path), now=1000, secret_ref="ref", resolver=lambda _r: _SECRET)
    assert st["configured"] and not st["healthy"] and "endpoint" in st["reason"]


def test_check_lifecycle_flags_expired_worktree_token(tmp_path: Path, wired_endpoint: str):
    """Le faux-négatif void-runner : un worktree porte un `.mcp.json` dont le token expire avant la fin d'un
    run → `stale`, `healthy=False` (doctor rougirait)."""
    settings = _settings(tmp_path)
    tok = mint_hs256("forgemaster:proj", _SECRET, audience="vault-catalogs", ttl_seconds=3600)
    wt = settings.projects_root / "proj" / "worktrees" / "feat"
    wt.mkdir(parents=True)
    (wt / ".mcp.json").write_text(json.dumps(mcp.render_mcp_config(tok, endpoint=wired_endpoint)),
                                  encoding="utf-8")
    future = 2_000_000_000                            # « maintenant » très futur → le token a expiré
    st = mcp.check_lifecycle(settings, now=future, secret_ref="ref", resolver=lambda _r: _SECRET)
    assert not st["healthy"] and len(st["stale"]) == 1 and str(wt) == st["stale"][0]["worktree"]


# -- rendu du .mcp.json -----------------------------------------------------------------------------

def test_render_mcp_config_shape():
    cfg = mcp.render_mcp_config("TOK", endpoint="http://h:8080/mcp")
    srv = cfg["mcpServers"]["vault-catalogs"]                # label = contrat prouvé (cf. provision/mcp.py)
    assert srv["type"] == "http" and srv["url"] == "http://h:8080/mcp"
    assert srv["headers"]["Authorization"] == "Bearer TOK"


def test_endpoint_resolved_live_not_frozen_at_import(monkeypatch: pytest.MonkeyPatch):
    """Fix 2026-07-30 : l'endpoint est résolu à l'APPEL (`current_endpoint`), jamais gelé à l'import. Un
    câblage live (`wire(live_env=True)` → `os.environ`) est donc reflété par `current_endpoint()` ET par le
    `.mcp.json` rendu sans endpoint explicite (`render_mcp_config`, le défaut du worker) — sans quoi un wire
    vers un endpoint ≠ défaut ne prenait pas effet sans redémarrer (503 « MCP non câblé »)."""
    monkeypatch.setenv(mcp.ENV_MCP_ENDPOINT, "http://live:9999/mcp")
    assert mcp.current_endpoint() == "http://live:9999/mcp"
    srv = mcp.render_mcp_config("TOK")["mcpServers"]["vault-catalogs"]
    assert srv["url"] == "http://live:9999/mcp"              # défaut résolu LIVE, pas la constante d'import


def test_no_endpoint_configured_is_none_not_our_ct(monkeypatch: pytest.MonkeyPatch):
    """Fix 2026-08-03 (prérequis de publication) : sans `FORGEMASTER_MCP_ENDPOINT`, l'endpoint est **`None`**.
    Le module portait un défaut en dur vers NOTRE CT — un défaut de produit qui ne survivait que parce que
    personne d'autre que nous ne l'exécutait. `render_mcp_config` refuse alors d'écrire une config muette."""
    monkeypatch.delenv(mcp.ENV_MCP_ENDPOINT, raising=False)
    assert mcp.current_endpoint() is None
    assert mcp.wire_state()["endpoint"] is None
    with pytest.raises(ValueError, match="aucun endpoint MCP"):
        mcp.render_mcp_config("TOK")


def test_empty_endpoint_is_treated_as_unconfigured(monkeypatch: pytest.MonkeyPatch):
    """`FORGEMASTER_MCP_ENDPOINT=` (posé mais vide — un `forgemaster.env` à moitié rempli) vaut NON configuré,
    pas
    une URL vide qu'on servirait au worker."""
    monkeypatch.setenv(mcp.ENV_MCP_ENDPOINT, "")
    assert mcp.current_endpoint() is None


# -- injection (façade sur le coffre) ---------------------------------------------------------------

def test_inject_writes_config_chmod600_with_scoped_bearer(tmp_path: Path, wired_endpoint: str):
    wt = tmp_path / "wt"
    wt.mkdir()
    p = mcp.inject_mcp_config(wt, _settings(tmp_path), slug="void-runner",
                              resolver=lambda ref: _SECRET, secret_ref="ref")
    assert p == wt / ".mcp.json" and p.exists()
    assert oct(p.stat().st_mode)[-3:] == "600"              # porte le Bearer → 600
    tok = json.loads(p.read_text())["mcpServers"]["vault-catalogs"]["headers"]["Authorization"]
    claims = verify_hs256(tok.removeprefix("Bearer "), _SECRET, audience="vault-catalogs")
    assert claims is not None and claims["sub"] == "forgemaster:void-runner"   # scopé au projet


def test_inject_is_honest_noop_without_ref_or_secret(tmp_path: Path, wired_endpoint: str):
    settings = _settings(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    # ref non configuré → None (install sans corpus privé : le worker tourne sans MCP)
    assert mcp.inject_mcp_config(wt, settings, slug="x", secret_ref="") is None
    # ref présent mais secret absent / trop court → None (jamais de crash de dispatch)
    assert mcp.inject_mcp_config(wt, settings, slug="x", resolver=lambda r: "", secret_ref="r") is None
    assert mcp.inject_mcp_config(wt, settings, slug="x", resolver=lambda r: "court", secret_ref="r") is None
    assert not (wt / ".mcp.json").exists()


def test_inject_is_honest_noop_without_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Secret parfaitement câblé mais **aucune cible** → no-op honnête (`None`, aucun fichier), jamais un
    `.mcp.json` sans URL ni un crash de dispatch. La forme produit de « pas d'endpoint = pas de MCP »."""
    monkeypatch.delenv(mcp.ENV_MCP_ENDPOINT, raising=False)
    wt = tmp_path / "wt"
    wt.mkdir()
    assert mcp.inject_mcp_config(wt, _settings(tmp_path), slug="x",
                                 resolver=lambda r: _SECRET, secret_ref="ref") is None
    assert not (wt / ".mcp.json").exists()


# -- invariant de sécurité : le Bearer ne peut JAMAIS être committé ---------------------------------

def test_injected_mcp_json_is_gitignored_never_committed(tmp_path: Path, wired_endpoint: str):
    """Le `.mcp.json` injecté dans un worktree réel est ignoré par git → le `git add -A` de la forge ne le
    stage pas (le JWT ne fuit jamais dans l'historique). C'est la garantie dure de P6."""
    settings = _settings(tmp_path)
    conn = store.open_db(settings)
    try:
        _seed_project(conn, settings)
        res = worktree.reserve(conn, settings, InternalGit(), project="proj", feature="feat", probe=None)
        wt = res["path"]
        assert (wt / ".gitignore").exists()                 # le bundle a bien semé le .gitignore
        p = mcp.inject_mcp_config(wt, settings, slug="proj", resolver=lambda r: _SECRET, secret_ref="ref")
        assert p is not None and p.exists()
        subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
        staged = subprocess.run(["git", "-C", str(wt), "diff", "--cached", "--name-only"],
                                capture_output=True, text=True, check=True).stdout
        assert ".mcp.json" not in staged                    # jamais stagé malgré `add -A`
        ci = subprocess.run(["git", "-C", str(wt), "check-ignore", ".mcp.json"],
                            capture_output=True, text=True)
        assert ci.returncode == 0                           # git l'ignore explicitement (preuve positive)
    finally:
        conn.close()


# -- câblage argv + chemin de dispatch réel ---------------------------------------------------------

def test_build_headless_argv_wires_mcp_config(tmp_path: Path):
    f = tmp_path / ".mcp.json"
    f.write_text("{}", encoding="utf-8")
    argv = worker.build_headless_argv(session_id="s", work=True, mcp_config=f)
    assert "--mcp-config" in argv and str(f) in argv
    # sans mcp_config → pas de flag
    assert "--mcp-config" not in worker.build_headless_argv(session_id="s", work=True)


def test_dispatch_injects_mcp_and_worker_sees_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 fake_tools, wired_endpoint: str):
    """Bout du chemin réel : secret dans le coffre fichier + ref via env → `dispatch_next` injecte le
    `.mcp.json` dans le worktree et passe `--mcp-config` au runner ; le worker le voit dans son cwd."""
    settings = _settings(tmp_path)
    conn = store.open_db(settings)
    try:
        fake_tools(settings)                 # hôte provisionné → le preflight de dispatch passe
        _seed_project(conn, settings)
        ref = build_store(settings).put(_SECRET, label="mcp-jwt")
        monkeypatch.setenv(mcp.ENV_MCP_JWT_SECRET_REF, ref)
        seen: dict = {}

        def runner(argv, *, cwd, input_text, timeout, env=None):
            seen["argv"] = list(argv)
            seen["mcp_present"] = (Path(cwd) / ".mcp.json").exists()
            sid = argv[argv.index("--session-id") + 1]
            out = json.dumps({"is_error": False, "result": "ok", "session_id": sid})
            return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

        report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=runner)
        assert report["dispatched"] and report["result"]["ok"]
        assert seen["mcp_present"] is True                  # le worker voit son .mcp.json
        assert "--mcp-config" in seen["argv"]
    finally:
        conn.close()


# -- upsert forgemaster.env (set_env_keys) --------------------------------------------------------------

def test_set_env_keys_adds_updates_and_preserves(tmp_path: Path):
    from forgemaster.service import set_env_keys
    env = tmp_path / "forgemaster.env"
    env.write_text("# en-tête\nFORGEMASTER_HOME=/x\n# FORGEMASTER_SECRET_STORE=bws\n", encoding="utf-8")
    set_env_keys(env, {"FORGEMASTER_MCP_ENDPOINT": "http://h/mcp"})     # clé neuve → en fin
    set_env_keys(env, {"FORGEMASTER_HOME": "/y"})                       # clé existante → écrasée sur place
    text = env.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "FORGEMASTER_HOME=/y" in lines and "FORGEMASTER_HOME=/x" not in lines   # pas de doublon
    assert text.count("FORGEMASTER_HOME=") == 1
    assert "FORGEMASTER_MCP_ENDPOINT=http://h/mcp" in lines
    assert "# en-tête" in lines and "# FORGEMASTER_SECRET_STORE=bws" in lines  # commentaires préservés
    assert (env.stat().st_mode & 0o777) == 0o600


def test_set_env_keys_creates_file_when_absent(tmp_path: Path):
    from forgemaster.service import set_env_keys
    env = tmp_path / "sub" / "forgemaster.env"
    set_env_keys(env, {"FORGEMASTER_MCP_JWT_SECRET_REF": "ref-abc"})
    assert env.read_text(encoding="utf-8").splitlines() == ["FORGEMASTER_MCP_JWT_SECRET_REF=ref-abc"]


# -- forgemaster mcp wire -------------------------------------------------------------------------------

def test_mcp_wire_file_path_persists_ref_never_plaintext(tmp_path: Path):
    import argparse
    settings = _settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(_SECRET, encoding="utf-8")
    rc = mcp.cli_dispatch(settings, argparse.Namespace(
        action="wire", secret_file=str(secret_file), secret_ref=None, endpoint=_ENDPOINT))
    assert rc == 0
    env_text = (settings.home / "forgemaster.env").read_text(encoding="utf-8")
    assert _SECRET not in env_text                                  # le secret n'est JAMAIS en clair
    assert f"FORGEMASTER_MCP_ENDPOINT={_ENDPOINT}" in env_text
    # la ref posée résout vers le secret dans le coffre
    ref = next(line.split("=", 1)[1] for line in env_text.splitlines()
               if line.startswith("FORGEMASTER_MCP_JWT_SECRET_REF="))
    assert build_store(settings).get(ref) == _SECRET


def test_mcp_wire_rejects_both_or_neither(tmp_path: Path):
    import argparse
    settings = _settings(tmp_path)
    both = mcp.cli_dispatch(settings, argparse.Namespace(
        action="wire", secret_file="x", secret_ref="y", endpoint=None))
    neither = mcp.cli_dispatch(settings, argparse.Namespace(
        action="wire", secret_file=None, secret_ref=None, endpoint=None))
    assert both == 2 and neither == 2                               # exactement-un imposé


def test_mcp_wire_ref_path_missing_ref_fails_and_writes_nothing(tmp_path: Path):
    import argparse
    settings = _settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)
    rc = mcp.cli_dispatch(settings, argparse.Namespace(          # endpoint FOURNI : le fail testé ici est
        action="wire", secret_file=None, secret_ref="00000000-inexistant", endpoint=_ENDPOINT))
    assert rc == 1                                                  # ref introuvable → fail-loud
    assert not (settings.home / "forgemaster.env").exists()             # rien posé


def test_mcp_wire_without_endpoint_refuses_and_writes_nothing(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch):
    """Câbler un secret sans dire vers QUOI produirait un câblage à moitié fait, silencieux au dispatch →
    refus **avant tout effet de bord** (rien dans le coffre, rien dans `forgemaster.env`)."""
    import argparse
    monkeypatch.delenv(mcp.ENV_MCP_ENDPOINT, raising=False)
    settings = _settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(_SECRET, encoding="utf-8")
    rc = mcp.cli_dispatch(settings, argparse.Namespace(
        action="wire", secret_file=str(secret_file), secret_ref=None, endpoint=None))
    assert rc == 1
    assert not (settings.home / "forgemaster.env").exists()             # aucun effet de bord
