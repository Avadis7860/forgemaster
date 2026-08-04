"""Tests de la couche `db/merge_outcomes` (v18, gate-green-outcome) : enregistrement dédupliqué du merge vert
(dénominateur), marque humaine de l'issue aval, pré-suggestion advisory dérivée au read, agrégation de
fiabilité (projet + global), et best-effort. DB `:memory:` migrée par `create_schema`."""
from __future__ import annotations

import sqlite3

import pytest

from forgemaster.core import ids
from forgemaster.db import alerts, merge_outcomes, schema


class _BoomConn:
    """Connexion factice dont l'écriture lève `sqlite3.Error` — prouve le best-effort de `record_merge`."""
    def execute(self, *a, **k):
        raise sqlite3.OperationalError("database is locked")

    def commit(self) -> None:
        pass


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    schema.create_schema(c)
    yield c
    c.close()


def _seed_chain(c: sqlite3.Connection, project: str, feature: str) -> str:
    """Insère projet → feature → task (FK valides) et renvoie le task_id (pour semer un dispatch_job)."""
    pid, fid, tid = ids.new_id(), ids.new_id(), ids.new_id()
    c.execute("INSERT INTO projects (id, slug, name, sot_path, created_at) VALUES (?,?,?,?,'t')",
              (pid, project, project.upper(), f"/sot/{project}"))
    c.execute("INSERT INTO features (id, project_id, slug, title, branch, created_at) "
              "VALUES (?,?,?,?,?,'t')", (fid, pid, feature, feature, f"feature/{feature}"))
    c.execute("INSERT INTO tasks (id, feature_id, slug, title, created_at) VALUES (?,?,?,?,'t')",
              (tid, fid, "t1", "t1"))
    c.commit()
    return tid


def _record(c, project="p", feature="f", sha="sha1", merged_at="2026-07-26T00:00:00+00:00", human_go=True):
    merge_outcomes.record_merge(c, project=project, feature=feature, feature_ref=f"{project}/{feature}",
                                sha=sha, human_go=human_go, merged_at=merged_at)


def test_record_merge_inserts_one_held(conn):
    _record(conn)
    rows = merge_outcomes.list_outcomes(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["outcome"] == "held" and r["human_go"] is True and r["marked_at"] is None
    assert r["sha"] == "sha1" and r["merged_at"] == "2026-07-26T00:00:00+00:00"


def test_record_merge_dedups_same_key(conn):
    """Deux merges au même `(project, feature, sha)` → UNE ligne (INSERT OR IGNORE) : le dénominateur ne
    double pas si le chokepoint rejoue."""
    _record(conn, sha="dup")
    _record(conn, sha="dup", merged_at="2026-07-26T09:00:00+00:00")
    rows = merge_outcomes.list_outcomes(conn)
    assert len(rows) == 1 and rows[0]["merged_at"] == "2026-07-26T00:00:00+00:00"   # 1re écriture préservée


def test_mark_outcome_held_to_reverted(conn):
    _record(conn)
    row = merge_outcomes.mark_outcome(conn, project="p", feature="f", outcome="reverted", note="régression",
                                      marked_at="2026-07-27T00:00:00+00:00")
    assert row["outcome"] == "reverted" and row["note"] == "régression"
    assert row["marked_at"] == "2026-07-27T00:00:00+00:00"
    # retour à held : marked_at ré-effacé
    back = merge_outcomes.mark_outcome(conn, project="p", feature="f", outcome="held")
    assert back["outcome"] == "held" and back["marked_at"] is None


def test_mark_outcome_targets_latest_without_sha(conn):
    _record(conn, sha="old", merged_at="2026-07-26T00:00:00+00:00")
    _record(conn, sha="new", merged_at="2026-07-26T10:00:00+00:00")
    merge_outcomes.mark_outcome(conn, project="p", feature="f", outcome="refixed")   # sans sha → le + récent
    by_sha = {r["sha"]: r["outcome"] for r in merge_outcomes.list_outcomes(conn)}
    assert by_sha == {"new": "refixed", "old": "held"}


def test_mark_outcome_unknown_raises(conn):
    with pytest.raises(KeyError):
        merge_outcomes.mark_outcome(conn, project="p", feature="ghost", outcome="reverted")


def test_list_outcomes_suggested_when_job_after_merge(conn):
    """Pré-suggestion advisory : un dispatch_job de la feature APRÈS `merged_at` → `suggested='refixed'`
    (nudge). Un job AVANT le merge ne suggère rien."""
    tid = _seed_chain(conn, "p", "f")
    _record(conn, merged_at="2026-07-26T00:00:00+00:00")
    # job antérieur au merge → pas de suggestion
    conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, started_at) VALUES (?,?,?,?)",
                 (ids.new_id(), tid, "/wt", "2026-07-25T00:00:00+00:00"))
    conn.commit()
    assert merge_outcomes.list_outcomes(conn, "p")[0]["suggested"] is None
    # job POSTÉRIEUR au merge → rework probable
    conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, started_at) VALUES (?,?,?,?)",
                 (ids.new_id(), tid, "/wt", "2026-07-28T00:00:00+00:00"))
    conn.commit()
    assert merge_outcomes.list_outcomes(conn, "p")[0]["suggested"] == "refixed"


def test_suggested_none_once_marked(conn):
    """La suggestion ne s'affiche que sur `held` : une ligne déjà marquée n'est plus nudgée."""
    tid = _seed_chain(conn, "p", "f")
    _record(conn, merged_at="2026-07-26T00:00:00+00:00")
    conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, started_at) VALUES (?,?,?,?)",
                 (ids.new_id(), tid, "/wt", "2026-07-28T00:00:00+00:00"))
    conn.commit()
    merge_outcomes.mark_outcome(conn, project="p", feature="f", outcome="reverted")
    assert merge_outcomes.list_outcomes(conn, "p")[0]["suggested"] is None


def test_reliability_project_taux_and_counts(conn):
    _seed_chain(conn, "p", "a")
    for i, feat in enumerate(("a", "b", "c")):
        _record(conn, feature=feat, sha=f"s{i}", merged_at=f"2026-07-26T0{i}:00:00+00:00")
    merge_outcomes.mark_outcome(conn, project="p", feature="a", outcome="reverted")
    r = merge_outcomes.reliability(conn, "p")
    assert r["scope"] == "project" and r["n_merges_verts"] == 3
    assert r["n_reverted"] == 1 and r["n_refixed"] == 0 and r["n_adverse"] == 1 and r["n_held"] == 2
    assert r["taux"] == round(2 / 3, 4)                     # fiabilité = verts_tenus / verts
    assert len(r["features"]) == 3


def test_reliability_empty_project_is_honest_none(conn):
    _seed_chain(conn, "p", "a")
    r = merge_outcomes.reliability(conn, "p")
    assert r["n_merges_verts"] == 0 and r["taux"] is None   # jamais 0/0


def test_reliability_unknown_project_raises(conn):
    with pytest.raises(KeyError):
        merge_outcomes.reliability(conn, "nope")


def test_reliability_global_rolls_up_by_project(conn):
    _record(conn, project="p", feature="a", sha="a")
    _record(conn, project="p", feature="b", sha="b")
    _record(conn, project="q", feature="c", sha="c")
    merge_outcomes.mark_outcome(conn, project="q", feature="c", outcome="reverted")
    g = merge_outcomes.reliability(conn, None)
    assert g["scope"] == "global" and g["n_merges_verts"] == 3 and g["n_adverse"] == 1
    assert g["taux"] == round(2 / 3, 4)
    by = {p["project"]: p for p in g["projects"]}
    assert by["p"]["taux"] == 1.0 and by["q"]["taux"] == 0.0


def test_reliability_provisional_when_no_adverse_mark(conn):
    """Honnêteté du signal : 2 merges verts, AUCUNE marque → `provisional=True` (le taux 100 % est vacant, il
    ne dit que « rien marqué mauvais »). Dès qu'une issue adverse est marquée → `provisional=False`."""
    _seed_chain(conn, "p", "a")
    _record(conn, feature="a", sha="a")
    _record(conn, feature="b", sha="b")
    r = merge_outcomes.reliability(conn, "p")
    assert r["taux"] == 1.0 and r["provisional"] is True and r["n_marked"] == 0 and r["n_held"] == 2
    merge_outcomes.mark_outcome(conn, project="p", feature="a", outcome="reverted")
    r2 = merge_outcomes.reliability(conn, "p")
    assert r2["provisional"] is False and r2["n_marked"] == 1


def test_reliability_tempered_by_open_blockers(conn):
    """Une feature 🔴-bloquée (alerte `gate_red` ouverte) n'entre JAMAIS dans `merge_outcomes` → invisible au
    taux. `reliability` la remonte à part (`n_blocked_open` + `blocked_features`) SANS l'injecter dans `taux`
    (le taux reste sur les seuls merges verts) — pour qu'un 100 % ne se lise pas « vert-santé » sur un projet
    qui bloque. Une alerte non-`gate_red` (ex. `review_findings`) ne compte pas comme blocker."""
    _seed_chain(conn, "p", "ok")
    _record(conn, project="p", feature="ok", sha="ok")     # 1 merge vert → taux 100 %
    alerts.emit_alert(conn, project="p", feature_ref="p/broken", feature="broken", kind="gate_red",
                      reason="Tier-1.5 rouge", severity="blocker")
    alerts.emit_alert(conn, project="p", feature_ref="p/ok", feature="ok", kind="review_findings",
                      reason="2 🟡", severity="info")
    r = merge_outcomes.reliability(conn, "p")
    assert r["taux"] == 1.0 and r["n_merges_verts"] == 1           # taux inchangé (verts seulement)
    assert r["n_blocked_open"] == 1                                # SEUL le gate_red compte
    assert [b["feature"] for b in r["blocked_features"]] == ["broken"]
    g = merge_outcomes.reliability(conn, None)                     # remonte aussi au global
    assert g["n_blocked_open"] == 1


def test_record_merge_best_effort_on_db_error():
    """Une erreur SQLite à l'écriture NE remonte PAS (best-effort : une trace ratée ne casse pas un merge)."""
    merge_outcomes.record_merge(_BoomConn(), project="p", feature="f", feature_ref="p/f", sha="x")
