"""Tests du câblage MCP (P6) : mint JWT stdlib, rendu du `.mcp.json`, injection au dispatch, et surtout
l'**invariant de sécurité** — le `.mcp.json` porteur du Bearer est gitignoré, donc le `git add -A` de la forge
ne peut jamais le committer. Runner injecté (aucun vrai `claude`), coffre fichier réel, worktree bare réel."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.dispatch import worker, worktree
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.provision import mcp
from cockpit.roadmap import model
from cockpit.secrets import build_store
from cockpit.secrets.jwt import mint_hs256, verify_hs256

_SECRET = "k" * 40  # ≥32c : exigence HS256


def _settings(tmp_path: Path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


def _seed_project(conn, settings, *, project="proj", feature="feat", task="t") -> None:
    registry.create_project(conn, settings, slug=project)
    model.add_feature(conn, project_slug=project, slug=feature)
    model.add_task(conn, feature_ref=f"{project}/{feature}", slug=task)


# -- mint JWT stdlib --------------------------------------------------------------------------------

def test_mint_and_verify_roundtrip():
    tok = mint_hs256("cockpit:void-runner", _SECRET, audience="vault-catalogs", issuer="vault-mcp",
                     ttl_seconds=60)
    claims = verify_hs256(tok, _SECRET, audience="vault-catalogs", issuer="vault-mcp")
    assert claims is not None
    assert claims["sub"] == "cockpit:void-runner"
    assert claims["aud"] == "vault-catalogs" and claims["iss"] == "vault-mcp"
    # mauvais secret / mauvaise audience → refusé (contrôle du contrat serveur)
    assert verify_hs256(tok, "x" * 40, audience="vault-catalogs") is None
    assert verify_hs256(tok, _SECRET, audience="autre") is None


def test_mint_rejects_short_secret():
    with pytest.raises(ValueError):
        mint_hs256("s", "trop-court", audience="vault-catalogs")


# -- rendu du .mcp.json -----------------------------------------------------------------------------

def test_render_mcp_config_shape():
    cfg = mcp.render_mcp_config("TOK", endpoint="http://h:8080/mcp")
    srv = cfg["mcpServers"]["vault-catalogs"]                # label = contrat prouvé (cf. provision/mcp.py)
    assert srv["type"] == "http" and srv["url"] == "http://h:8080/mcp"
    assert srv["headers"]["Authorization"] == "Bearer TOK"


# -- injection (façade sur le coffre) ---------------------------------------------------------------

def test_inject_writes_config_chmod600_with_scoped_bearer(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    p = mcp.inject_mcp_config(wt, _settings(tmp_path), slug="void-runner",
                              resolver=lambda ref: _SECRET, secret_ref="ref")
    assert p == wt / ".mcp.json" and p.exists()
    assert oct(p.stat().st_mode)[-3:] == "600"              # porte le Bearer → 600
    tok = json.loads(p.read_text())["mcpServers"]["vault-catalogs"]["headers"]["Authorization"]
    claims = verify_hs256(tok.removeprefix("Bearer "), _SECRET, audience="vault-catalogs")
    assert claims is not None and claims["sub"] == "cockpit:void-runner"   # scopé au projet


def test_inject_is_honest_noop_without_ref_or_secret(tmp_path: Path):
    settings = _settings(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    # ref non configuré → None (install sans corpus privé : le worker tourne sans MCP)
    assert mcp.inject_mcp_config(wt, settings, slug="x", secret_ref="") is None
    # ref présent mais secret absent / trop court → None (jamais de crash de dispatch)
    assert mcp.inject_mcp_config(wt, settings, slug="x", resolver=lambda r: "", secret_ref="r") is None
    assert mcp.inject_mcp_config(wt, settings, slug="x", resolver=lambda r: "court", secret_ref="r") is None
    assert not (wt / ".mcp.json").exists()


# -- invariant de sécurité : le Bearer ne peut JAMAIS être committé ---------------------------------

def test_injected_mcp_json_is_gitignored_never_committed(tmp_path: Path):
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
                                                 fake_tools):
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


# -- upsert cockpit.env (set_env_keys) --------------------------------------------------------------

def test_set_env_keys_adds_updates_and_preserves(tmp_path: Path):
    from cockpit.service import set_env_keys
    env = tmp_path / "cockpit.env"
    env.write_text("# en-tête\nCOCKPIT_HOME=/x\n# COCKPIT_SECRET_STORE=bws\n", encoding="utf-8")
    set_env_keys(env, {"COCKPIT_MCP_ENDPOINT": "http://h/mcp"})     # clé neuve → en fin
    set_env_keys(env, {"COCKPIT_HOME": "/y"})                       # clé existante → écrasée sur place
    text = env.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "COCKPIT_HOME=/y" in lines and "COCKPIT_HOME=/x" not in lines   # pas de doublon
    assert text.count("COCKPIT_HOME=") == 1
    assert "COCKPIT_MCP_ENDPOINT=http://h/mcp" in lines
    assert "# en-tête" in lines and "# COCKPIT_SECRET_STORE=bws" in lines  # commentaires préservés
    assert (env.stat().st_mode & 0o777) == 0o600


def test_set_env_keys_creates_file_when_absent(tmp_path: Path):
    from cockpit.service import set_env_keys
    env = tmp_path / "sub" / "cockpit.env"
    set_env_keys(env, {"COCKPIT_MCP_JWT_SECRET_REF": "ref-abc"})
    assert env.read_text(encoding="utf-8").splitlines() == ["COCKPIT_MCP_JWT_SECRET_REF=ref-abc"]


# -- cockpit mcp wire -------------------------------------------------------------------------------

def test_mcp_wire_file_path_persists_ref_never_plaintext(tmp_path: Path):
    import argparse
    settings = _settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(_SECRET, encoding="utf-8")
    rc = mcp.cli_dispatch(settings, argparse.Namespace(
        action="wire", secret_file=str(secret_file), secret_ref=None, endpoint="http://192.168.0.153:8080/mcp"))
    assert rc == 0
    env_text = (settings.home / "cockpit.env").read_text(encoding="utf-8")
    assert _SECRET not in env_text                                  # le secret n'est JAMAIS en clair
    assert "COCKPIT_MCP_ENDPOINT=http://192.168.0.153:8080/mcp" in env_text
    # la ref posée résout vers le secret dans le coffre
    ref = next(line.split("=", 1)[1] for line in env_text.splitlines()
               if line.startswith("COCKPIT_MCP_JWT_SECRET_REF="))
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
    rc = mcp.cli_dispatch(settings, argparse.Namespace(
        action="wire", secret_file=None, secret_ref="00000000-inexistant", endpoint=None))
    assert rc == 1                                                  # ref introuvable → fail-loud
    assert not (settings.home / "cockpit.env").exists()             # rien posé
