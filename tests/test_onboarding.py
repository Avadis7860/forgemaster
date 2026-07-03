"""Tests onboarding — check de config-requise (`status`) + liaison/déliaison de credential, sur DB + store
jetables. Fil rouge non négociable : la DB ne reçoit que la **référence**, jamais le token en clair."""
from __future__ import annotations

from pathlib import Path

import pytest

from cockpit import onboarding
from cockpit.config import Settings
from cockpit.db import store
from cockpit.projects import registry
from cockpit.secrets import SecretNotFound, SecretUnsupported
from cockpit.secrets.file_store import EncryptedFileStore

_MIRROR = "https://github.com/x/y.git"


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


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
