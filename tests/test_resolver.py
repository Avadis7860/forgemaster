"""Tests du résolveur DAG — désormais un **adaptateur mince sur `taskmap.core`** (dé-fork P1). La surface
publique (`classify`/`eff_prio`/`resolve_next`/`PRIO`) et le **contrat JSON cockpit** (états + blockers en
vocab cockpit) sont préservés byte-identiques ; seul le moteur sous-jacent a changé. On vérifie la
classification, le cycle/dangling, `eff_prio` transitif, le NEXT, et la **re-traduction** des blockers
(statut original conservé malgré la projection `in_progress`→`active` côté taskmap)."""
from __future__ import annotations

import argparse
from pathlib import Path

from cockpit.config import Settings
from cockpit.db import store
from cockpit.projects import registry
from cockpit.roadmap import model, resolver


def _t(slug: str, *, status: str = "todo", deps=(), prio: str = "P1", created: str = "2026-01-01") -> dict:
    return {"slug": slug, "status": status, "depends_on": list(deps), "priority": prio,
            "created_at": created, "title": slug}


def _index(*tasks: dict) -> dict[str, dict]:
    return {t["slug"]: t for t in tasks}


def test_classify_ready_and_blocked_deps():
    idx = _index(_t("a"), _t("b", deps=["a"]))
    c = resolver.classify(idx)
    assert c["a"]["state"] == "READY"
    assert c["b"]["state"] == "BLOCKED_DEPS"
    assert c["b"]["blockers"] == ["a (todo)"]
    # a done → b devient READY
    idx["a"]["status"] = "done"
    c = resolver.classify(idx)
    assert c["b"]["state"] == "READY"


def test_classify_dangling_is_error_and_cycle_is_cycle():
    assert resolver.classify(_index(_t("a", deps=["ghost"])))["a"]["state"] == "ERROR"
    cyc = resolver.classify(_index(_t("a", deps=["b"]), _t("b", deps=["a"])))
    assert cyc["a"]["state"] == "CYCLE" and cyc["b"]["state"] == "CYCLE"


def test_blocker_preserves_original_status_for_in_progress_dep():
    # Une dép `in_progress` → BLOCKED_DEPS ; le blocker porte le statut ORIGINAL cockpit (« in_progress »),
    # PAS le vocab taskmap (« active ») vers lequel l'adaptateur projette pour la décision d'état.
    idx = _index(_t("a", status="in_progress"), _t("b", deps=["a"]))
    c = resolver.classify(idx)
    assert c["b"]["state"] == "BLOCKED_DEPS"
    assert c["b"]["blockers"] == ["a (in_progress)"]   # re-traduction : contrat cockpit préservé


def test_classify_terminal_states_take_precedence():
    idx = _index(
        _t("d", status="done", deps=["ghost"]),      # done l'emporte sur le dangling
        _t("x", status="cancelled"),
        _t("p", status="in_progress"),
        _t("b", status="blocked"),
    )
    c = resolver.classify(idx)
    assert c["d"]["state"] == "DONE"
    assert c["x"]["state"] == "CANCELLED"
    assert c["p"]["state"] == "ACTIVE"
    assert c["b"]["state"] == "BLOCKED"


def test_eff_prio_transitive_lifts_unblocker():
    # a (P3) débloque c (P0) ; d (P2) indépendante. a doit remonter devant d.
    idx = _index(_t("a", prio="P3"), _t("c", prio="P0", deps=["a"]), _t("d", prio="P2"))
    effp = resolver.eff_prio(idx)
    assert effp["a"] == resolver.PRIO["P0"]   # hérite de son dépendant c
    assert effp["d"] == resolver.PRIO["P2"]
    nxt = resolver.resolve_next(idx)
    assert nxt["slug"] == "a"                 # READY: {a, d} ; a (eff P0) < d (P2)


def test_resolve_next_none_when_all_blocked_or_done():
    idx = _index(_t("a", status="done"), _t("b", deps=["a"], status="done"))
    assert resolver.resolve_next(idx) is None
    idx2 = _index(_t("a"), _t("b", deps=["a"]))
    idx2["a"]["status"] = "in_progress"       # a ACTIVE (pas READY), b bloquée
    assert resolver.resolve_next(idx2) is None


def test_next_tiebreak_is_slug_stable():
    idx = _index(_t("zeta", prio="P1", created="2026-01-01"), _t("alpha", prio="P1", created="2026-01-01"))
    assert resolver.resolve_next(idx)["slug"] == "alpha"   # même prio+date → tiebreak slug


def test_task_add_cli_uses_priority_and_requires_acceptance(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    conn.close()
    # priorité transmise (plus de P1 figé en dur)
    rc = resolver.cli_dispatch(settings, argparse.Namespace(
        action="add", feature="proj/feat", slug="t1", title=None, depends_on=[],
        priority="P0", acceptance="critère binaire testé"))
    assert rc == 0
    conn = store.open_db(settings)
    feat = model.resolve_feature(conn, "proj/feat")
    assert model.list_tasks(conn, feat["id"])[0]["priority"] == "P0"
    conn.close()
    # acceptance vide/blanche → rc 1 (task non créée)
    rc = resolver.cli_dispatch(settings, argparse.Namespace(
        action="add", feature="proj/feat", slug="t2", title=None, depends_on=[],
        priority="P1", acceptance="   "))
    assert rc == 1


def test_index_for_feature_and_next_e2e(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug="schema", priority="P1")
    model.add_task(conn, feature_ref="proj/feat", slug="api", depends_on=["schema"], priority="P0")
    idx = resolver.index_for_feature(conn, "proj/feat")
    # api (P0) est bloquée par schema ; schema est la seule READY → NEXT même si prio inférieure,
    # mais eff_prio remonte schema à P0 (il débloque api).
    nxt = resolver.resolve_next(idx)
    assert nxt["slug"] == "schema"
    assert resolver.eff_prio(idx)["schema"] == resolver.PRIO["P0"]
    conn.close()
