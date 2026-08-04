"""scoped_cred_resolver — ACL par projet (P4) : le résolveur d'un projet ne résout QUE son propre
`credential_ref`, jamais le token d'un voisin. Durcissement control-plane de l'anti-pollution : deux projets
partagent le store global, mais pas la visibilité de leurs secrets."""
from __future__ import annotations

from pathlib import Path

import pytest

from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.projects import registry
from forgemaster.secrets import build_store, cred_resolver, scoped_cred_resolver


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


def _project_with_secret(settings, conn, *, slug: str, value: str) -> str:
    """Crée un projet + range un secret dans le store global et le lie au projet. Retourne le ref opaque."""
    registry.create_project(conn, settings, slug=slug)
    ref = build_store(settings).put(value, label=f"tok:{slug}")
    registry.set_credential_ref(conn, slug, ref)
    return ref


def test_scoped_resolver_resolves_own_ref(ctx):
    settings, conn = ctx
    ref_a = _project_with_secret(settings, conn, slug="alpha", value="tok-alpha")
    assert scoped_cred_resolver(settings, conn, slug="alpha")(ref_a) == "tok-alpha"


def test_scoped_resolver_refuses_another_projects_ref(ctx):
    """Le cœur du DoD P4 : le service/contexte de A ne peut PAS résoudre le secret de B — refus silencieux
    (`''`), jamais le token d'autrui, alors même que le store global le contient."""
    settings, conn = ctx
    _project_with_secret(settings, conn, slug="alpha", value="tok-alpha")
    ref_b = _project_with_secret(settings, conn, slug="beta", value="tok-beta")
    # A demande le ref de B : refusé, bien que le store SACHE le résoudre (le resolver global, lui, le rend).
    assert scoped_cred_resolver(settings, conn, slug="alpha")(ref_b) == ""
    assert cred_resolver(settings)(ref_b) == "tok-beta"          # contraste : sans ACL, tout ref résout


def test_scoped_resolver_total_on_unknown_project_or_empty_ref(ctx):
    settings, conn = ctx
    ref_a = _project_with_secret(settings, conn, slug="alpha", value="tok-alpha")
    assert scoped_cred_resolver(settings, conn, slug="ghost")(ref_a) == ""   # projet inconnu → '' (total)
    assert scoped_cred_resolver(settings, conn, slug="alpha")("") == ""      # ref vide → '' (jamais lève)


def test_scoped_resolver_refuses_when_project_has_no_linked_ref(ctx):
    """Un projet sans `credential_ref` lié ne résout rien — même pas un ref valide d'un autre projet."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="alpha")            # aucun secret lié
    ref_b = _project_with_secret(settings, conn, slug="beta", value="tok-beta")
    assert scoped_cred_resolver(settings, conn, slug="alpha")(ref_b) == ""
