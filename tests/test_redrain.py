"""Tests de `redrain` — recouvrement first-class d'une feature à base périmée non-réalignable (A1).

Réutilise les primitives RÉELLES (InternalGit, worktrees vrais) : on réserve un worktree pour une feature
« drainée-mais-bloquée », on la re-draine, et on vérifie l'état résultant — worktree purgé (branche
réinitialisée sur `dev` au prochain reserve via `add_worktree -B`), tasks `todo`, feature `planned`, alertes
stale résolues, zéro orphelin (`worktree.audit`) — + idempotence + KeyError sur feature absente."""
from __future__ import annotations

from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.db import alerts, store
from cockpit.dispatch import redrain
from cockpit.dispatch import worktree as worktree_mod
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.projects.registry import sot_path_for
from cockpit.roadmap import model


@pytest.fixture
def ctx(tmp_path: Path, fake_tools):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    fake_tools(settings)
    yield settings, conn
    conn.close()


def _new_project(conn, settings, slug: str) -> None:
    registry.create_project(conn, settings, slug=slug)
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")
    conn.commit()


def _seed(conn, project: str, feature: str, tasks: list[tuple[str, list[str]]]) -> None:
    model.add_feature(conn, project_slug=project, slug=feature)
    for slug, deps in tasks:
        model.add_task(conn, feature_ref=f"{project}/{feature}", slug=slug, depends_on=deps)


def _statuses(conn, feature: str) -> dict[str, str]:
    return {r["slug"]: r["status"] for r in conn.execute(
        "SELECT t.slug, t.status FROM tasks t JOIN features f ON t.feature_id = f.id WHERE f.slug = ?",
        (feature,))}


def _reserve_and_stick(conn, settings, git, project: str, feature: str) -> dict:
    """Simule une feature DRAINÉE-mais-bloquée : réserve son worktree (→ status `active`), marque ses tasks
    `done`, pose une alerte worker-level (le blocage que le re-drain doit recouvrir)."""
    res = worktree_mod.reserve(conn, settings, git, project=project, feature=feature)
    conn.execute("UPDATE tasks SET status = 'done' WHERE feature_id = "
                 "(SELECT id FROM features WHERE slug = ? AND project_id = "
                 "(SELECT id FROM projects WHERE slug = ?))", (feature, project))
    conn.commit()
    alerts.emit_alert(conn, project=project, feature_ref=f"{project}/{feature}", feature=feature,
                      kind="worker_failed", severity="blocker", reason="base périmée — re-drain requis")
    return res


def test_redrain_resets_worktree_tasks_status_and_alerts(ctx):
    """Le re-drain remet la feature à zéro sur `dev` : worktree purgé, tasks `todo`, feature `planned`,
    alerte worker-level résolue, zéro orphelin (port relâché avec le worktree)."""
    settings, conn = ctx
    git = InternalGit()
    _new_project(conn, settings, "proj")
    _seed(conn, "proj", "feat", [("t1", []), ("t2", ["t1"])])
    res = _reserve_and_stick(conn, settings, git, "proj", "feat")
    assert Path(res["path"]).exists()                                   # worktree bien créé

    report = redrain.redrain_feature(conn, settings, project="proj", feature="feat", git=git)

    assert report["redrained"] is True and report["tasks_reset"] == 2
    assert _statuses(conn, "feat") == {"t1": "todo", "t2": "todo"}      # DAG remis à drainer
    assert model.resolve_feature(conn, "proj/feat")["status"] == "planned"
    assert not Path(res["path"]).exists()                              # worktree purgé
    assert worktree_mod.audit(conn, settings, git) == []               # zéro orphelin (port relâché aussi)
    assert not any(a["kind"] == "worker_failed"                        # alerte stale résolue
                   for a in alerts.list_alerts(conn, "open"))


def test_redrain_then_reserve_recreates_worktree_on_dev(ctx):
    """Le débloqueur : après re-drain, un `reserve` suivant re-crée le worktree SANS conflit (`add_worktree
    -B` réinitialise la branche sur `dev`) → la feature repart `active`, `dev` ancêtre de sa branche."""
    settings, conn = ctx
    git = InternalGit()
    _new_project(conn, settings, "proj")
    _seed(conn, "proj", "feat", [("t", [])])
    _reserve_and_stick(conn, settings, git, "proj", "feat")
    redrain.redrain_feature(conn, settings, project="proj", feature="feat", git=git)

    res2 = worktree_mod.reserve(conn, settings, git, project="proj", feature="feat")   # doit réussir

    assert Path(res2["path"]).exists()
    assert model.resolve_feature(conn, "proj/feat")["status"] == "active"   # reserve repose active
    assert git.is_ancestor(sot_path_for(settings, "proj"), "dev", "feature/feat")   # base saine


def test_redrain_is_idempotent(ctx):
    """Deux re-drains d'affilée ne lèvent pas et laissent un état cohérent (worktree déjà purgé, tasks déjà
    `todo`) — le recouvrement est rejouable."""
    settings, conn = ctx
    git = InternalGit()
    _new_project(conn, settings, "proj")
    _seed(conn, "proj", "feat", [("t", [])])
    _reserve_and_stick(conn, settings, git, "proj", "feat")
    redrain.redrain_feature(conn, settings, project="proj", feature="feat", git=git)

    report2 = redrain.redrain_feature(conn, settings, project="proj", feature="feat", git=git)

    assert report2["redrained"] is True
    assert _statuses(conn, "feat") == {"t": "todo"}
    assert worktree_mod.audit(conn, settings, git) == []


def test_redrain_unknown_feature_raises_keyerror(ctx):
    """Feature absente → `KeyError` (mappé 404 côté route), jamais un faux succès."""
    settings, conn = ctx
    _new_project(conn, settings, "proj")
    with pytest.raises(KeyError):
        redrain.redrain_feature(conn, settings, project="proj", feature="ghost", git=InternalGit())
