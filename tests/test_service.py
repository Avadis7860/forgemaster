"""Tests de `service` — rendu de l'unité systemd (pur) + installation (écrit l'unité + un env gabarit sans
écraser une conf existante). On n'active jamais systemctl en test (effet système)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from forgemaster import service
from forgemaster.config import Settings


def _settings(tmp: Path) -> Settings:
    return Settings.resolve(home=tmp / "home", projects_root=tmp / "projects")


def test_render_unit_pins_home_and_execstart(tmp_path: Path):
    unit = service.render_unit(_settings(tmp_path), host="0.0.0.0", port=8712, scope="user")
    assert "Environment=HOME=" in unit                       # sinon git ne lit pas le helper (fetch muet)
    assert "forgemaster serve --host 0.0.0.0 --port 8712" in unit
    assert "EnvironmentFile=-" in unit and "forgemaster.env" in unit
    assert "WantedBy=default.target" in unit                 # portée user
    assert "User=" not in unit                                # user scope : pas de User=


def test_render_unit_system_scope_adds_identity(tmp_path: Path):
    unit = service.render_unit(_settings(tmp_path), host="127.0.0.1", port=8700, scope="system")
    assert "User=" in unit and "Group=" in unit
    assert "WantedBy=multi-user.target" in unit


def test_render_unit_rejects_bad_scope(tmp_path: Path):
    with pytest.raises(ValueError):
        service.render_unit(_settings(tmp_path), host="127.0.0.1", port=8700, scope="nope")


def test_install_service_writes_unit_and_env_without_clobber(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))   # unité user → ~/.config/systemd/user
    settings = _settings(tmp_path)
    # env pré-existant : ne doit PAS être écrasé
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "forgemaster.env").write_text("FORGEMASTER_HOME=custom\n")

    unit, env, hint = service.install_service(settings, host="0.0.0.0", port=8700, scope="user")
    assert unit.is_file() and unit.name == "forgemaster.service"
    assert env.read_text() == "FORGEMASTER_HOME=custom\n"        # conf existante préservée
    assert "systemctl --user" in hint and "enable --now forgemaster" in hint


def test_install_service_seeds_env_template_when_absent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    settings = _settings(tmp_path)
    _unit, env, _hint = service.install_service(settings, host="127.0.0.1", port=8700, scope="user")
    body = env.read_text()
    assert "FORGEMASTER_HOME=" in body and "FORGEMASTER_SECRET_STORE" in body


def test_load_env_file_populates_environ_without_override(tmp_path: Path, monkeypatch):
    env = tmp_path / "forgemaster.env"
    env.write_text("# commentaire\n\nFORGEMASTER_MCP_JWT_SECRET_REF=ref-opaque-123\n"
                   "FORGEMASTER_MCP_ENDPOINT=http://file:8080/mcp\n", encoding="utf-8")
    monkeypatch.delenv("FORGEMASTER_MCP_JWT_SECRET_REF", raising=False)
    monkeypatch.setenv("FORGEMASTER_MCP_ENDPOINT", "http://shell-wins:9/mcp")   # déjà dans l'env réel
    try:
        loaded = service.load_env_file(env)
        assert os.environ["FORGEMASTER_MCP_JWT_SECRET_REF"] == "ref-opaque-123"    # posée depuis le fichier
        # NON écrasée (shell gagne)
        assert os.environ["FORGEMASTER_MCP_ENDPOINT"] == "http://shell-wins:9/mcp"
        assert loaded == {"FORGEMASTER_MCP_JWT_SECRET_REF": "ref-opaque-123"}       # seule la clé neuve posée
    finally:
        # load écrit os.environ direct → nettoyage manuel
        os.environ.pop("FORGEMASTER_MCP_JWT_SECRET_REF", None)


def test_load_env_file_noop_when_absent(tmp_path: Path):
    assert service.load_env_file(tmp_path / "nope.env") == {}
