"""Tests du client MCP runtime (`cockpit.mcp.blueprint_resolver`) — **aucun réseau**, seams injectés.

Prouve la **dégradation honnête totale** (secret absent/court, MCP down, réponse vide → `None`, jamais
d'exception) et le **hit** (dict rendu tel quel). Un dernier test branche le resolver au seam réel
`taskmap.context._blueprint_verdict` (le contrat que consommera le board P3) : `resolved:true`+fusion sur hit,
`resolved:false`+liaison morte sur down — le mint HS256 réel est exercé (secret factice ≥32).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from taskmap.context import _blueprint_verdict

from cockpit.config import Settings
from cockpit.mcp import blueprint_resolver

_SECRET = "x" * 40                      # ≥32 → le mint HS256 réel passe
_FAKE_REF = "ref-opaque"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


def _ok_secret(_ref: str) -> str:
    return _SECRET


# -- dégradation honnête : toujours None, jamais d'exception -----------------------------------------

def test_no_secret_ref_returns_none_without_calling_mcp(settings, monkeypatch):
    monkeypatch.delenv("COCKPIT_MCP_JWT_SECRET_REF", raising=False)
    called: list = []

    def caller(*a, **k):
        called.append(1)
        return {"id": "g"}

    resolve = blueprint_resolver(settings, caller=caller, resolver=_ok_secret)
    assert resolve("some-gate") is None      # ref absent → MCP non câblé
    assert called == []                      # caller JAMAIS atteint (pas de secret → pas d'appel)


def test_short_secret_returns_none_without_mint(settings):
    called: list = []

    def caller(*a, **k):
        called.append(1)
        return {"id": "g"}

    resolve = blueprint_resolver(settings, secret_ref=_FAKE_REF,
                                 resolver=lambda _r: "trop-court", caller=caller)
    assert resolve("some-gate") is None      # secret <32 → no-op
    assert called == []


def test_mcp_down_is_swallowed_to_none(settings):
    def caller(*a, **k):
        raise ConnectionError("MCP injoignable")

    resolve = blueprint_resolver(settings, secret_ref=_FAKE_REF, resolver=_ok_secret, caller=caller)
    assert resolve("some-gate") is None      # exception réseau → None, jamais propagée


def test_empty_response_is_none(settings):
    resolve = blueprint_resolver(settings, secret_ref=_FAKE_REF, resolver=_ok_secret,
                                 caller=lambda *a, **k: {})
    assert resolve("some-gate") is None      # dict vide → None (→ liaison morte côté taskmap)
    resolve_none = blueprint_resolver(settings, secret_ref=_FAKE_REF, resolver=_ok_secret,
                                      caller=lambda *a, **k: None)
    assert resolve_none("some-gate") is None


def test_hit_returns_blueprint_body(settings):
    body = {"id": "some-gate", "title": "Le gate déterministe", "status": "current", "posture": "applies"}
    seen: dict = {}

    def caller(endpoint, token, bp_id, *, timeout):
        seen.update(endpoint=endpoint, token=token, bp_id=bp_id, timeout=timeout)
        return body

    resolve = blueprint_resolver(settings, secret_ref=_FAKE_REF, resolver=_ok_secret, caller=caller,
                                 endpoint="http://mcp.test/mcp")
    assert resolve("some-gate") == body
    assert seen["bp_id"] == "some-gate" and seen["endpoint"] == "http://mcp.test/mcp"
    assert seen["token"].count(".") == 2     # un JWT (header.body.sig) réellement minté


# -- branchement au seam réel de taskmap (le contrat du board P3) ------------------------------------

def test_plugs_into_taskmap_seam_hit(settings):
    body = {"id": "some-gate", "title": "Le gate", "status": "current"}
    resolve = blueprint_resolver(settings, secret_ref=_FAKE_REF, resolver=_ok_secret,
                                 caller=lambda *a, **k: body)
    v = _blueprint_verdict({"id": "some-gate", "posture": "applies"}, resolve)
    assert v is not None
    assert v["resolved"] is True and v["title"] == "Le gate"     # champs fusionnés
    assert v["id"] == "some-gate" and v["posture"] == "applies"  # clés protégées préservées


def test_plugs_into_taskmap_seam_down(settings):
    def caller(*a, **k):
        raise TimeoutError("MCP timeout")

    resolve = blueprint_resolver(settings, secret_ref=_FAKE_REF, resolver=_ok_secret, caller=caller)
    v = _blueprint_verdict({"id": "some-gate", "posture": None}, resolve)
    assert v is not None
    assert v["resolved"] is False and v["reason"]                # liaison morte signalée, jamais inventé
