"""build_store — sélection du backend d'après Settings.secret_store + résolution de la config."""
from __future__ import annotations

import pytest

from forgemaster.config import Settings
from forgemaster.secrets import SecretStoreError, build_store
from forgemaster.secrets.base import SecretStore
from forgemaster.secrets.bws_store import BwsStore
from forgemaster.secrets.file_store import EncryptedFileStore


def _settings(tmp_path, backend="file"):
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "proj", secret_store=backend)


def test_default_backend_is_file(tmp_path, monkeypatch):
    monkeypatch.delenv("FORGEMASTER_SECRET_STORE", raising=False)
    settings = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    assert settings.secret_store == "file"
    store = build_store(settings)
    assert isinstance(store, EncryptedFileStore)
    assert isinstance(store, SecretStore)  # conforme au Protocol runtime-checkable


def test_file_store_rooted_in_secrets_dir(tmp_path):
    settings = _settings(tmp_path, "file")
    assert settings.secrets_dir == settings.home / "secrets"
    ref = build_store(settings).put("v")
    assert (settings.secrets_dir / "store.enc").exists()
    assert build_store(settings).get(ref) == "v"


def test_bws_backend_selected(tmp_path):
    store = build_store(_settings(tmp_path, "bws"))
    assert isinstance(store, BwsStore)


def test_env_selects_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGEMASTER_SECRET_STORE", "bws")
    settings = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    assert settings.secret_store == "bws"


def test_unknown_backend_raises(tmp_path):
    with pytest.raises(SecretStoreError):
        build_store(_settings(tmp_path, "vault-xyz"))
