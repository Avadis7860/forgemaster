"""Tests des déploiements (v7 runtime-hosting) : registry `projects/deployments` (ensure/list/set + cascade),
migration v6→v7 (table neuve créée en place), et la route read-only `GET /api/projects/{p}/deployments`."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cockpit.config import Settings
from cockpit.daemon import app as app_mod
from cockpit.db import schema, store
from cockpit.projects import deployments, registry


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return TestClient(app_mod.build_app(settings)), settings


# -- registry : ensure / list / set / cascade ------------------------------------------------------

def test_create_project_seeds_both_deployments_no_deploy(ctx):
    settings, conn = ctx
    p = registry.create_project(conn, settings, slug="svc")
    rows = deployments.list_deployments(conn, p["id"])
    assert [d["branch"] for d in rows] == ["main", "dev"]         # prod d'abord, ordre stable
    assert all(d["status"] == "no_deploy" for d in rows)          # vide honnête, jamais un faux-vert
    assert all(d["port"] is None and d["url"] is None and d["last_deploy_sha"] is None for d in rows)


def test_ensure_deployments_is_idempotent(ctx):
    settings, conn = ctx
    p = registry.create_project(conn, settings, slug="proj")
    deployments.ensure_deployments(conn, p["id"])                 # ré-appel : aucune duplication
    deployments.ensure_deployments(conn, p["id"])
    rows = deployments.list_deployments(conn, p["id"])
    assert len(rows) == 2                                         # exactement 2 (UNIQUE project,branch)


def test_set_deployment_partial_update_leaves_siblings_and_fields_intact(ctx):
    settings, conn = ctx
    p = registry.create_project(conn, settings, slug="proj")
    updated = deployments.set_deployment(conn, p["id"], "dev", status="running", port=5170,
                                         url="http://127.0.0.1:5170", last_deploy_sha="abc123")
    assert updated["status"] == "running" and updated["port"] == 5170
    assert updated["url"] == "http://127.0.0.1:5170" and updated["last_deploy_sha"] == "abc123"
    assert updated["compose_ref"] is None                        # champ non fourni → inchangé (None)
    # `main` intact (l'upsert est scopé à (projet, branche))
    main = deployments.get_deployment(conn, p["id"], "main")
    assert main["status"] == "no_deploy" and main["port"] is None


def test_set_deployment_rejects_bad_branch_and_missing_row(ctx):
    settings, conn = ctx
    p = registry.create_project(conn, settings, slug="proj")
    with pytest.raises(ValueError, match="branche invalide"):
        deployments.set_deployment(conn, p["id"], "prod", status="running")   # hors {main,dev}
    with pytest.raises(KeyError):
        deployments.set_deployment(conn, "no-such-project", "dev", status="running")  # aucune row


def test_deployments_cascade_deleted_with_project(ctx):
    settings, conn = ctx
    p = registry.create_project(conn, settings, slug="doomed")
    assert len(deployments.list_deployments(conn, p["id"])) == 2
    conn.execute("DELETE FROM projects WHERE id = ?", (p["id"],))              # FK ON DELETE CASCADE
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM deployments WHERE project_id = ?",
                        (p["id"],)).fetchone()[0] == 0


# -- migration v6→v7 : table neuve créée en place ---------------------------------------------------

def test_migration_v6_to_v7_creates_deployments_table_in_place(tmp_path: Path):
    """Une base v6 (sans table `deployments`) migre en place : `store.migrate` (via `create_schema` +
    `CREATE IF NOT EXISTS`) crée la table neuve et pose `SCHEMA_VERSION=7` — sans `ensure_columns`."""
    conn = store.connect(tmp_path / "v6.db")
    # base « v6 » minimale : la table projects existe, deployments PAS ENCORE, version marquée 6
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT "
                 "NULL, sot_path TEXT NOT NULL, backend TEXT NOT NULL DEFAULT 'internal', "
                 "created_at TEXT NOT NULL)")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    tables_before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "deployments" not in tables_before

    assert store.migrate(conn) == 7                              # migre → version cible
    tables_after = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "deployments" in tables_after
    assert schema.schema_version(conn) == 7
    conn.close()


# -- route read-only -------------------------------------------------------------------------------

def test_deployments_endpoint_returns_both_no_deploy(client):
    c, _ = client
    assert c.post("/api/projects", json={"slug": "proj"}).status_code == 201
    r = c.get("/api/projects/proj/deployments")
    assert r.status_code == 200
    body = r.json()
    assert body["project"] == "proj"
    deps = body["deployments"]
    assert [d["branch"] for d in deps] == ["main", "dev"]         # main (prod) avant dev (preview)
    assert all(d["status"] == "no_deploy" for d in deps)         # vide honnête
    assert set(deps[0]) == {"branch", "status", "port", "url", "last_deploy_sha"}   # surface publique
    assert all(d["port"] is None and d["url"] is None for d in deps)


def test_deployments_endpoint_404_for_unknown_project(client):
    c, _ = client
    assert c.get("/api/projects/ghost/deployments").status_code == 404   # KeyError → 404 (handler global)
