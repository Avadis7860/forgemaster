"""Tests onboarding — check de config-requise (`status`) + liaison/déliaison de credential, sur DB + store
jetables. Fil rouge non négociable : la DB ne reçoit que la **référence**, jamais le token en clair."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cockpit import onboarding
from cockpit.config import Settings
from cockpit.db import store
from cockpit.projects import registry
from cockpit.provision import mcp
from cockpit.secrets import SecretNotFound, SecretUnsupported
from cockpit.secrets.file_store import EncryptedFileStore

_MIRROR = "https://github.com/x/y.git"


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


@pytest.fixture
def mcp_env(monkeypatch):
    """Isole l'env MCP vivant (départ « non câblé »). monkeypatch **POSSÈDE** les clés via `setenv` → même si
    `wire()` les écrit en dur (`live_env`), le teardown les supprime → zéro fuite vers les tests voisins (ex.
    `doctor`). NB : `delenv` sur une clé absente n'enregistre RIEN, d'où la fuite qu'on évite ici."""
    for key in (mcp.ENV_MCP_JWT_SECRET_REF, "COCKPIT_MCP_ENDPOINT"):
        monkeypatch.setenv(key, "")     # "" → wire_state lit `wired=False` ; teardown supprime la clé
    return monkeypatch


class _FakeBws:
    """SecretStore BWS factice (bring-your-own UUID) : `get` valide une réf connue, `put`/`delete` non
    supportés — reproduit le contrat de `BwsStore` sans SDK ni réseau."""

    backend = "bws"

    def __init__(self, known: dict[str, str], *, ready: bool = True) -> None:
        self._known = dict(known)
        self._ready = ready

    def get(self, ref: str) -> str:
        if ref not in self._known:
            raise SecretNotFound(ref)
        return self._known[ref]

    def put(self, value: str, *, label: str | None = None) -> str:
        raise SecretUnsupported("BWS bring-your-own")

    def delete(self, ref: str) -> None:
        raise SecretUnsupported("BWS bring-your-own")

    def has(self, ref: str) -> bool:
        return ref in self._known

    def list_entries(self) -> list[dict[str, str | None]]:
        return []

    def health(self) -> tuple[bool, str]:
        return self._ready, "token présent" if self._ready else "BWS_ACCESS_TOKEN absent"


def test_status_reports_store_health_and_per_project_requirements(ctx):
    settings, conn = ctx
    fs = EncryptedFileStore(settings.secrets_dir)
    registry.create_project(conn, settings, slug="plain")                       # pas de miroir → 0 besoin
    registry.create_project(conn, settings, slug="mirrored", mirror_remote=_MIRROR)
    st = onboarding.status(conn, fs)
    assert st["secret_store"] == {"backend": "file", "ready": True,
                                  "detail": st["secret_store"]["detail"]}        # file = zéro-config, prêt
    reqs = {r["project"]: r for r in st["requirements"]}
    assert reqs["plain"]["needs_credential"] is False and reqs["plain"]["satisfied"] is True
    assert reqs["mirrored"]["needs_credential"] is True and reqs["mirrored"]["satisfied"] is False
    assert st["complete"] is False                                              # mirrored manque un token


def test_status_first_run_on_empty_instance_then_flips(ctx):
    settings, conn = ctx
    fs = EncryptedFileStore(settings.secrets_dir)
    st = onboarding.status(conn, fs)
    assert st["first_run"] is True and st["project_count"] == 0     # instance neuve → wizard guide
    registry.create_project(conn, settings, slug="first")
    st = onboarding.status(conn, fs)
    assert st["first_run"] is False and st["project_count"] == 1                 # un projet → plus « neuve »


def test_status_carries_build_block_via_injection(ctx):
    settings, conn = ctx
    fs = EncryptedFileStore(settings.secrets_dir)
    fake = {"version": "0.1.0", "sha": "abc", "committed_at": None,
            "comparable": True, "stale": True, "behind_by": 2, "missing_types": ["site-vitrine"]}
    st = onboarding.status(conn, fs, build_state=fake)
    assert st["build"] == fake                                     # le signal traverse tel quel (injection)


def test_status_build_incomparable_without_local_mirror(ctx):
    settings, conn = ctx
    fs = EncryptedFileStore(settings.secrets_dir)
    st = onboarding.status(conn, fs, settings=settings)            # projects_root/cockpit/sot.git absent
    assert st["build"]["comparable"] is False                     # honnête, aucun faux-vert, aucune levée
    assert "version" in st["build"]                               # provenance seule exposée tout de même


def test_link_file_store_keeps_value_in_store_and_only_ref_in_db(ctx):
    settings, conn = ctx
    fs = EncryptedFileStore(settings.secrets_dir)
    registry.create_project(conn, settings, slug="mirrored", mirror_remote=_MIRROR)
    p = onboarding.link_credential(conn, fs, "mirrored", token="ghp_SECRET", label="gh")
    ref = p["credential_ref"]
    assert ref and "ghp_SECRET" not in ref                                     # réf opaque, jamais le token
    assert fs.get(ref) == "ghp_SECRET"                                         # la valeur vit dans le store
    assert b"ghp_SECRET" not in settings.db_path.read_bytes()                  # 0 token en DB (invariant)
    assert onboarding.status(conn, fs)["complete"] is True                     # exigence satisfaite
    # délier remet la réf à NULL (le secret reste dans le store, non piloté ici)
    onboarding.unlink_credential(conn, "mirrored")
    assert registry.get_project(conn, "mirrored")["credential_ref"] is None
    assert onboarding.status(conn, fs)["complete"] is False


def test_link_requires_exactly_one_of_token_or_ref_and_valid_project(ctx):
    settings, conn = ctx
    fs = EncryptedFileStore(settings.secrets_dir)
    registry.create_project(conn, settings, slug="p")
    with pytest.raises(ValueError):
        onboarding.link_credential(conn, fs, "p")                              # ni token ni ref
    with pytest.raises(ValueError):
        onboarding.link_credential(conn, fs, "p", token="t", ref="r")          # les deux
    with pytest.raises(KeyError):
        onboarding.link_credential(conn, fs, "absent", token="t")             # projet inconnu → 404


def test_link_bws_validates_uuid_and_rejects_direct_token(ctx):
    settings, conn = ctx
    bws = _FakeBws({"uuid-ok": "secret-value"})
    registry.create_project(conn, settings, slug="p")
    with pytest.raises(ValueError):                                            # bring-your-own : pas de put
        onboarding.link_credential(conn, bws, "p", token="ghp_x")
    with pytest.raises(ValueError):                                            # réf inconnue → validée avant
        onboarding.link_credential(conn, bws, "p", ref="uuid-absent")
    p = onboarding.link_credential(conn, bws, "p", ref="uuid-ok")             # réf valide → liée telle quelle
    assert p["credential_ref"] == "uuid-ok"


def test_status_incomplete_when_store_root_unreachable(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="p")                          # aucun miroir → 0 exigence
    st = onboarding.status(conn, _FakeBws({}, ready=False))
    assert st["secret_store"]["ready"] is False and st["complete"] is False    # racine injoignable → pas vert


def test_status_reports_mcp_wire_state_default_unwired_and_injected(ctx, mcp_env):
    settings, conn = ctx
    fs = EncryptedFileStore(settings.secrets_dir)
    st = onboarding.status(conn, fs)
    assert st["mcp"]["wired"] is False                                         # install publique : non câblé
    assert st["complete"] is True                                              # MCP optionnel → hors complete
    st2 = onboarding.status(conn, fs, mcp_state={"wired": True, "endpoint": "http://ep/mcp"})
    assert st2["mcp"] == {"wired": True, "endpoint": "http://ep/mcp"}          # état injectable (tests)


def test_cli_status_surfaces_mcp_line_unwired_then_wired(ctx, mcp_env, capsys):
    """DoD 3a : `cockpit onboard status` **imprime** l'état MCP (déjà dans le dict, jamais surfacé avant).
    Non câblé → ligne `ℹ️ … à connecter` (visible et compréhensible, jamais un vide muet) ; câblé → `✅ …
    câblé (<endpoint>)`. MCP optionnel : la ligne n'est jamais 🔴."""
    import argparse
    settings, _ = ctx
    args = argparse.Namespace(action="status")
    onboarding.cli_dispatch(settings, args)                                    # mcp_env = "" → non câblé
    out = capsys.readouterr().out
    assert "corpus capital (MCP)" in out and "à connecter" in out
    assert "🔴 corpus capital" not in out                                     # optionnel → jamais bloquant
    mcp_env.setenv(mcp.ENV_MCP_JWT_SECRET_REF, "ref-xyz")                      # câblé (env vivant)
    mcp_env.setenv("COCKPIT_MCP_ENDPOINT", "http://ep/mcp")
    onboarding.cli_dispatch(settings, args)
    out2 = capsys.readouterr().out
    assert "corpus capital (MCP) — câblé" in out2 and "http://ep/mcp" in out2


def test_wire_mcp_value_stores_ref_persists_and_reflects_live_env(ctx, mcp_env):
    settings, _ = ctx
    secret = "x" * 40                                                          # ≥32c : HS256 l'exige
    res = onboarding.wire_mcp(settings, secret=secret, endpoint="http://ep/mcp")
    ref = res["credential_ref"]
    assert res["wired"] is True and ref and secret not in ref                  # ref opaque, jamais la valeur
    assert EncryptedFileStore(settings.secrets_dir).get(ref) == secret         # la valeur vit dans le store
    # live_env : le daemon voit ref + endpoint SANS restart, et c'est aussi persisté dans cockpit.env
    assert os.environ[mcp.ENV_MCP_JWT_SECRET_REF] == ref
    assert os.environ["COCKPIT_MCP_ENDPOINT"] == "http://ep/mcp"
    assert ref in (settings.home / "cockpit.env").read_text(encoding="utf-8")
    with pytest.raises(ValueError):                                            # <32c → refusé (HS256)
        onboarding.wire_mcp(settings, secret="short")


def test_wire_mcp_requires_exactly_one_voie_and_validates_bws_ref(ctx, mcp_env):
    settings, _ = ctx
    ep = "http://mcp.example/mcp"                                               # endpoint explicite : chaque
    bws = _FakeBws({"uuid-ok": "shared-hmac-secret"})                           # refus ci-dessous doit tomber
    mcp_env.setattr(mcp, "build_store", lambda _s: bws)                         # pour SA raison, pas faute de
    with pytest.raises(ValueError):                                            # cible (cf. test dédié)
        onboarding.wire_mcp(settings, endpoint=ep)                             # ni secret ni ref
    with pytest.raises(ValueError):
        onboarding.wire_mcp(settings, secret="s" * 40, ref="uuid-ok", endpoint=ep)  # les deux → mauvais usage
    with pytest.raises(ValueError):
        onboarding.wire_mcp(settings, secret="s" * 40, endpoint=ep)            # BWS : pas de put d'une valeur
    with pytest.raises(ValueError):
        onboarding.wire_mcp(settings, ref="uuid-absent", endpoint=ep)          # réf inconnue → validée avant
    res = onboarding.wire_mcp(settings, ref="uuid-ok", endpoint=ep)            # réf valide → posée telle
    assert res["credential_ref"] == "uuid-ok"
    assert res["endpoint"] == ep
    assert os.environ[mcp.ENV_MCP_JWT_SECRET_REF] == "uuid-ok"


def test_wire_mcp_without_endpoint_is_a_400(ctx, mcp_env):
    """Depuis le 2026-08-03 le produit n'a plus de cible MCP en dur : le wizard qui câble sans endpoint reçoit
    un 400 explicite (message humain réutilisé tel quel), pas un câblage silencieux vers notre CT."""
    settings, _ = ctx
    mcp_env.delenv("COCKPIT_MCP_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="aucun endpoint MCP"):
        onboarding.wire_mcp(settings, secret="x" * 40)
    assert not (settings.home / "cockpit.env").exists()                         # aucun effet de bord
