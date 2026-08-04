"""Tests de l'auth Claude de l'hôte : détection déterministe (présence, jamais la valeur), surface
onboarding, et **gate de dispatch** (refus explicite avant tout spawn — jamais d'usage silencieux d'un
compte hérité)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from forgemaster import auth, onboarding
from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.dispatch import worker
from forgemaster.secrets.file_store import EncryptedFileStore

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


# -- trust workspace (le dispatch marque le SoT trusted → claude honore les allowedTools) -------------

def test_trust_workspace_creates_and_upserts(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "proj" / "sot.git"
    p = auth.trust_workspace(ws, home=home)
    assert p == home / ".claude.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["projects"][str(ws)]["hasTrustDialogAccepted"] is True


def test_trust_workspace_preserves_keys_and_idempotent(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(
        json.dumps({"userID": "u", "projects": {"/other": {"hasTrustDialogAccepted": True}}}),
        encoding="utf-8")
    ws = tmp_path / "proj" / "sot.git"
    auth.trust_workspace(ws, home=home)
    auth.trust_workspace(ws, home=home)                                   # idempotent
    data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert data["userID"] == "u"                                          # clés préexistantes préservées
    assert data["projects"]["/other"]["hasTrustDialogAccepted"] is True   # autre projet intact
    assert data["projects"][str(ws)]["hasTrustDialogAccepted"] is True


def test_trust_workspace_tolerates_corrupt_json(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text("{ pas du json", encoding="utf-8")
    ws = tmp_path / "proj" / "sot.git"
    auth.trust_workspace(ws, home=home)                          # ne lève pas : repart d'un dict vide
    data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert data["projects"][str(ws)]["hasTrustDialogAccepted"] is True


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
