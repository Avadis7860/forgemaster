"""Tests de l'auth Claude de l'hôte : détection déterministe (présence, jamais la valeur), surface
onboarding, et **gate de dispatch** (refus explicite avant tout spawn — jamais d'usage silencieux d'un
compte hérité)."""
from __future__ import annotations

import argparse
from pathlib import Path

from cockpit import auth, onboarding
from cockpit.config import Settings
from cockpit.db import store
from cockpit.dispatch import worker
from cockpit.secrets.file_store import EncryptedFileStore

# -- détection (sans jamais lire le secret) ---------------------------------------------------------

def test_auth_detects_credentials_file(tmp_path: Path):
    cred = tmp_path / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text('{"whatever": "..."}', encoding="utf-8")     # présence = signal, la valeur n'est pas lue
    assert auth.claude_auth_status(home=tmp_path, env={}) == {
        "authenticated": True, "source": "credentials-file"}


def test_auth_detects_env_keys(tmp_path: Path):
    assert auth.claude_auth_status(
        home=tmp_path, env={"ANTHROPIC_API_KEY": "sk-x"})["source"] == "env-api-key"
    assert auth.claude_auth_status(
        home=tmp_path, env={"CLAUDE_CODE_OAUTH_TOKEN": "t"})["source"] == "env-oauth"


def test_auth_absent_when_no_file_no_env(tmp_path: Path):
    assert auth.claude_auth_status(home=tmp_path, env={}) == {"authenticated": False, "source": None}


# -- surface onboarding (axe orthogonal à `complete`) -----------------------------------------------

def test_onboarding_status_carries_claude_auth(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    conn = store.open_db(settings)
    fs = EncryptedFileStore(settings.secrets_dir)
    st = onboarding.status(conn, fs, claude_auth_state={"authenticated": False, "source": None})
    assert st["claude_auth"] == {"authenticated": False, "source": None}
    # orthogonal : sans miroir ni exigence, `complete` reste vrai même si l'auth manque (axes séparés)
    assert st["complete"] is True
    conn.close()


# -- gate de dispatch (refus AVANT tout spawn) ------------------------------------------------------

def test_dispatch_cli_refuses_without_auth(tmp_path: Path, capsys, monkeypatch):
    settings = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    monkeypatch.setattr(auth, "claude_auth_status",
                        lambda *a, **k: {"authenticated": False, "source": None})
    code = worker.cli_dispatch(settings, argparse.Namespace(feature="proj/feat"))
    assert code == 2                                    # refus net (≠ 1 = erreur métier)
    assert "claude login" in capsys.readouterr().out   # message actionnable, voie officielle


def test_dispatch_cli_passes_gate_when_authed(tmp_path: Path, capsys, monkeypatch):
    # auth présente → le gate laisse passer ; le refus vient alors de la feature absente, pas de l'auth
    settings = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    monkeypatch.setattr(auth, "claude_auth_status",
                        lambda *a, **k: {"authenticated": True, "source": "test"})
    code = worker.cli_dispatch(settings, argparse.Namespace(feature="proj/absent"))
    assert code == 1                                    # erreur métier (feature absente), PAS le gate 2
    assert "claude login" not in capsys.readouterr().out
