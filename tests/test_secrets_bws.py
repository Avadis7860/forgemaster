"""BwsStore — via un client factice injecté (aucun SDK ni réseau) : résolution, cache, non-supporté, token."""
from __future__ import annotations

import pytest

from cockpit.secrets.base import SecretNotFound, SecretStoreError, SecretUnsupported
from cockpit.secrets.bws_store import BwsStore


class _FakeResult:
    def __init__(self, *, success, value=None, error_message=None):
        self.success = success
        self.error_message = error_message
        self.data = type("D", (), {"value": value})() if success else None


class _FakeSecrets:
    def __init__(self, table, counter):
        self._table = table
        self._counter = counter

    def get(self, ref):
        self._counter["calls"] += 1
        if ref in self._table:
            return _FakeResult(success=True, value=self._table[ref])
        return _FakeResult(success=False, error_message="Resource not found (404)")


class _FakeClient:
    def __init__(self, table, counter):
        self._secrets = _FakeSecrets(table, counter)

    def secrets(self):
        return self._secrets


def _factory(table):
    counter = {"calls": 0, "auth": 0}

    def make(access_token, api_url, identity_url):
        counter["auth"] += 1
        assert access_token  # le token racine a bien été résolu
        return _FakeClient(table, counter)

    return make, counter


def test_get_resolves_by_uuid():
    make, _ = _factory({"uuid-1": "sekret"})
    store = BwsStore(access_token="tok", client_factory=make)
    assert store.get("uuid-1") == "sekret"


def test_get_missing_maps_to_not_found():
    make, _ = _factory({})
    store = BwsStore(access_token="tok", client_factory=make)
    with pytest.raises(SecretNotFound):
        store.get("absent")


def test_get_caches_and_auths_once():
    """Perf : après le 1er get, la valeur est en cache ; l'auth (login) n'a lieu qu'une fois."""
    make, counter = _factory({"uuid-1": "sekret"})
    store = BwsStore(access_token="tok", client_factory=make)
    store.get("uuid-1")
    store.get("uuid-1")
    assert counter["calls"] == 1  # 2ᵉ get servi par le cache
    assert counter["auth"] == 1  # client construit/authentifié une seule fois


def test_put_and_delete_unsupported():
    make, _ = _factory({})
    store = BwsStore(access_token="tok", client_factory=make)
    with pytest.raises(SecretUnsupported):
        store.put("v")
    with pytest.raises(SecretUnsupported):
        store.delete("r")


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "from-env")
    make, counter = _factory({"u": "v"})
    assert BwsStore(client_factory=make).get("u") == "v"
    assert counter["auth"] == 1


def test_token_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    tf = tmp_path / "bws-token"
    tf.write_text("file-tok\n")
    make, _ = _factory({"u": "v"})
    assert BwsStore(token_file=tf, client_factory=make).get("u") == "v"


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BWS_ACCESS_TOKEN_FILE", raising=False)
    make, _ = _factory({"u": "v"})
    with pytest.raises(SecretStoreError):
        BwsStore(client_factory=make).get("u")
