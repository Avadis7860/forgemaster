"""Tests du gate de complétude `cockpit roadmap check` : classe les défauts qui rendent une roadmap
non-drainable (deps dangling, cycle, DoD manquante, facette manquante/invalide, vide) en réutilisant le
DAG du résolveur. Sémantique de gate : `cli_dispatch` retourne 1 dès une issue, 0 sinon."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.db import store
from cockpit.projects import registry
from cockpit.roadmap import check, model


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


def _kinds(issues) -> list[str]:
    return sorted(i.kind for i in issues)


def test_healthy_roadmap_has_no_issues(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")   # facets: frontend/backend
    model.add_feature(conn, project_slug="proj", slug="api", facet="backend")
    model.add_task(conn, feature_ref="proj/api", slug="schema",
                   acceptance="Le endpoint /health répond 200, test inclus.")
    model.add_task(conn, feature_ref="proj/api", slug="route", depends_on=["schema"],
                   acceptance="GET /users renvoie la liste, test inclus.")
    assert check.check_roadmap(conn, "proj") == []          # BLOCKED_DEPS est normal, pas une issue


def test_empty_project_then_empty_feature(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    assert _kinds(check.check_roadmap(conn, "proj")) == ["EMPTY"]        # projet sans feature
    model.add_feature(conn, project_slug="proj", slug="api", facet="backend")
    assert "EMPTY" in _kinds(check.check_roadmap(conn, "proj"))          # feature sans task


def test_missing_acceptance_is_flagged(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="api", facet="backend")
    model.add_task(conn, feature_ref="proj/api", slug="schema")          # aucune DoD
    issues = check.check_roadmap(conn, "proj")
    assert any(i.kind == "MISSING_ACCEPTANCE" and i.task == "schema" for i in issues)


def test_dangling_dep_is_flagged(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="api", facet="backend")
    model.add_task(conn, feature_ref="proj/api", slug="schema", depends_on=["ghost"], acceptance="x")
    assert any(i.kind == "DANGLING_DEP" for i in check.check_roadmap(conn, "proj"))


def test_cycle_is_flagged(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="api", facet="backend")
    model.add_task(conn, feature_ref="proj/api", slug="a", depends_on=["b"], acceptance="x")
    model.add_task(conn, feature_ref="proj/api", slug="b", depends_on=["a"], acceptance="x")
    assert "CYCLE" in _kinds(check.check_roadmap(conn, "proj"))


def test_missing_facet_is_flagged(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="api")             # facet omis → NULL
    model.add_task(conn, feature_ref="proj/api", slug="schema", acceptance="x")
    assert any(i.kind == "MISSING_FACET" and i.feature == "api"
               for i in check.check_roadmap(conn, "proj"))


def test_bad_facet_after_bundle_drift_is_flagged(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="api", facet="backend")
    model.add_task(conn, feature_ref="proj/api", slug="schema", acceptance="x")
    # simule une dérive : le bundle ne déclare plus cette facette (UPDATE direct, hors API d'authoring)
    conn.execute("UPDATE features SET facet = 'widget' WHERE slug = 'api'")
    conn.commit()
    assert any(i.kind == "BAD_FACET" for i in check.check_roadmap(conn, "proj"))


def test_cli_dispatch_exit_codes(ctx, capsys):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="api", facet="backend")
    model.add_task(conn, feature_ref="proj/api", slug="schema", acceptance="x")
    rc = check.cli_dispatch(settings, argparse.Namespace(project="proj"))
    assert rc == 0
    assert "opérationnelle" in capsys.readouterr().out
    model.add_task(conn, feature_ref="proj/api", slug="broke")           # casse la DoD
    rc = check.cli_dispatch(settings, argparse.Namespace(project="proj"))
    assert rc == 1
    assert "MISSING_ACCEPTANCE" in capsys.readouterr().out
