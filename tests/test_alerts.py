"""Tests de la couche `db/alerts` (v17, no-silent-block) : émission dédupliquée (UPSERT sur index partiel),
résolution (open→resolved), lecture (open-only, newest-first), acquittement, et best-effort. DB `:memory:`
migrée par `create_schema`."""
from __future__ import annotations

import sqlite3

import pytest

from forgemaster.db import alerts, schema


class _BoomConn:
    """Connexion factice dont l'écriture lève `sqlite3.Error` — pour prouver le best-effort d'`emit_alert`."""
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


def _open(c) -> list[dict]:
    return alerts.list_alerts(c, "open")


def test_emit_inserts_one_open_alert(conn):
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="gate_red",
                      reason="Tier-1 : aucune revue", tier="tier1", findings=["b1", "b2"],
                      created_at="2026-07-26T00:00:00Z")
    rows = _open(conn)
    assert len(rows) == 1
    a = rows[0]
    assert a["kind"] == "gate_red" and a["reason"] == "Tier-1 : aucune revue" and a["tier"] == "tier1"
    assert a["severity"] == "blocker" and a["status"] == "open" and a["resolved_at"] is None
    assert a["findings"] == ["b1", "b2"]                    # JSON re-parsé en objet


def test_emit_twice_same_key_dedups_and_refreshes(conn):
    """Deux détections du même `(project, feature_ref, kind)` → UNE seule ligne ouverte (index partiel) :
    `reason`/`updated_at` rafraîchis, `created_at` (1re détection) PRÉSERVÉ."""
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="worker_failed",
                      reason="boom-1", created_at="2026-07-26T00:00:00Z")
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="worker_failed",
                      reason="boom-2", created_at="2026-07-26T01:00:00Z")
    rows = _open(conn)
    assert len(rows) == 1
    assert rows[0]["reason"] == "boom-2"                    # dernière détection gagne
    assert rows[0]["created_at"] == "2026-07-26T00:00:00Z"  # 1re détection préservée
    assert rows[0]["updated_at"] == "2026-07-26T01:00:00Z"  # rafraîchi


def test_distinct_kinds_coexist(conn):
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="gate_red", reason="r")
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="worker_failed", reason="r")
    assert {a["kind"] for a in _open(conn)} == {"gate_red", "worker_failed"}   # motifs distincts = 2 lignes


def test_resolve_flips_open_to_resolved(conn):
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="gate_red", reason="r")
    alerts.resolve_alerts(conn, project="p", feature_ref="p/f", resolved_at="2026-07-26T02:00:00Z")
    assert _open(conn) == []
    resolved = alerts.list_alerts(conn, "resolved")
    assert len(resolved) == 1 and resolved[0]["resolved_at"] == "2026-07-26T02:00:00Z"


def test_resolve_scoped_by_kinds(conn):
    """`resolve_alerts(kinds=…)` ne ferme QUE les motifs visés — `gate_red` a un cycle distinct des motifs
    worker-level (résolus au drainage)."""
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="gate_red", reason="r")
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="worker_failed", reason="r")
    alerts.resolve_alerts(conn, project="p", feature_ref="p/f", kinds=("worker_failed",))
    assert {a["kind"] for a in _open(conn)} == {"gate_red"}   # seul worker_failed résolu


def test_reemit_after_resolve_opens_a_fresh_row(conn):
    """Une ligne résolue sort de l'index unique partiel → une re-blocage ré-ouvre proprement (pas de
    collision `UNIQUE`)."""
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="gate_red", reason="r1")
    alerts.resolve_alerts(conn, project="p", feature_ref="p/f", kinds=("gate_red",))
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="gate_red", reason="r2")
    rows = _open(conn)
    assert len(rows) == 1 and rows[0]["reason"] == "r2"
    assert len(alerts.list_alerts(conn, "resolved")) == 1   # l'ancienne reste historisée


def test_list_alerts_open_only_newest_first(conn):
    alerts.emit_alert(conn, project="p", feature_ref="p/a", feature="a", kind="gate_red", reason="r",
                      created_at="2026-07-26T00:00:00Z")
    alerts.emit_alert(conn, project="p", feature_ref="p/b", feature="b", kind="gate_red", reason="r",
                      created_at="2026-07-26T05:00:00Z")
    rows = _open(conn)
    assert [a["feature"] for a in rows] == ["b", "a"]        # plus récent d'abord


def test_ack_flips_open_to_acked_and_unknown_raises(conn):
    alerts.emit_alert(conn, project="p", feature_ref="p/f", feature="f", kind="gate_red", reason="r")
    aid = _open(conn)[0]["id"]
    acked = alerts.ack_alert(conn, aid)
    assert acked["status"] == "acked" and _open(conn) == []
    with pytest.raises(KeyError):
        alerts.ack_alert(conn, "does-not-exist")


def test_emit_and_resolve_are_best_effort_on_db_error():
    """Une erreur SQLite à l'écriture NE remonte PAS (best-effort : une alerte/résolution ratée ne casse
    jamais un drain ni un merge)."""
    boom = _BoomConn()
    alerts.emit_alert(boom, project="p", feature_ref="p/f", feature="f", kind="gate_red", reason="r")
    alerts.resolve_alerts(boom, project="p", feature_ref="p/f")   # aucune exception attendue
