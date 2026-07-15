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
    conn.execute("DELETE FROM features")   # vide la roadmap de lancement semée → teste le cas board vide
    conn.commit()
    assert _kinds(check.check_roadmap(conn, "proj")) == ["EMPTY"]        # projet sans feature
    model.add_feature(conn, project_slug="proj", slug="api", facet="backend")
    assert "EMPTY" in _kinds(check.check_roadmap(conn, "proj"))          # feature sans task


@pytest.mark.parametrize("project_type", ["generic", "browser-game"])
def test_seeded_launch_roadmap_passes_check(ctx, project_type):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type=project_type)
    assert check.check_roadmap(conn, "proj") == []      # la roadmap de lancement semée est opérationnelle


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


def test_feature_dep_healthy_is_no_issue(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="design", facet="backend")
    model.add_task(conn, feature_ref="proj/design", slug="spec", acceptance="x")
    model.add_feature(conn, project_slug="proj", slug="code", facet="backend", depends_on=["design"])
    model.add_task(conn, feature_ref="proj/code", slug="impl", acceptance="x")
    # dep inter-feature valide ; prérequis pas encore mergé = BLOCKED_DEPS, NORMAL, pas une issue.
    assert check.check_roadmap(conn, "proj") == []


def test_dangling_feature_dep_is_flagged(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="code", facet="backend", depends_on=["ghost"])
    model.add_task(conn, feature_ref="proj/code", slug="impl", acceptance="x")
    assert any(i.kind == "DANGLING_FEATURE_DEP" and i.feature == "code"
               for i in check.check_roadmap(conn, "proj"))


def test_feature_cycle_is_flagged(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="a", facet="backend", depends_on=["b"])
    model.add_task(conn, feature_ref="proj/a", slug="ta", acceptance="x")
    model.add_feature(conn, project_slug="proj", slug="b", facet="backend", depends_on=["a"])
    model.add_task(conn, feature_ref="proj/b", slug="tb", acceptance="x")
    assert "FEATURE_CYCLE" in _kinds(check.check_roadmap(conn, "proj"))


def test_dead_feature_dep_on_cancelled_prereq_is_flagged(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="design", facet="backend")
    model.add_task(conn, feature_ref="proj/design", slug="spec", acceptance="x")
    model.add_feature(conn, project_slug="proj", slug="code", facet="backend", depends_on=["design"])
    model.add_task(conn, feature_ref="proj/code", slug="impl", acceptance="x")
    conn.execute("UPDATE features SET status = 'cancelled' WHERE slug = 'design'")
    conn.commit()
    # prérequis annulé → le dépendant ne peut jamais se débloquer : surfacé, pas de deadlock silencieux.
    assert any(i.kind == "DEAD_FEATURE_DEP" and i.feature == "code"
               for i in check.check_roadmap(conn, "proj"))


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
