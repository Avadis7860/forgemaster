"""Tests de l'orchestrateur parallèle (`cockpit run`) : drainage du DAG intra-feature, parallélisme borné
inter-features, mutex par feature, tolérance à l'échec, terminaison. DB **fichier** (les workers ouvrent
leur propre connexion) + git réel (InternalGit, worktrees vrais, flock) + runner INJECTÉ (aucun `claude`).
Le runner instrumenté mesure la concurrence réelle (pic global + pic par feature) pendant son délai."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.dispatch import orchestrator
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.roadmap import model


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)          # base FICHIER : les threads workers rouvrent settings.db_path
    yield settings, conn
    conn.close()


class _Runner:
    """Runner injecté : renvoie un résultat `claude -p` synthétique, échoue pour les features de `fail`, et
    **mesure la concurrence** — pic global (`peak`) et pic par feature (`feature_peak`, dérivé du nom du
    worktree = `cwd`) — en tenant un délai bloquant qui force le chevauchement réel des runs en vol."""
    def __init__(self, *, fail: tuple[str, ...] = (), delay: float = 0.06):
        self.fail = set(fail)
        self.delay = delay
        self._lock = threading.Lock()
        self._active = 0
        self._per: dict[str, int] = {}
        self.peak = 0
        self.feature_peak = 0
        self.calls: list[str] = []          # features appelées, dans l'ordre d'entrée

    def __call__(self, argv, *, cwd, input_text, timeout):
        feature = Path(cwd).name
        with self._lock:
            self._active += 1
            self._per[feature] = self._per.get(feature, 0) + 1
            self.peak = max(self.peak, self._active)
            self.feature_peak = max(self.feature_peak, self._per[feature])
            self.calls.append(feature)
        try:
            time.sleep(self.delay)          # fenêtre de chevauchement observable
            sid = argv[argv.index("--session-id") + 1]
            if feature in self.fail:
                return run.RunResult(argv=list(argv), returncode=1, stdout="boom", stderr="err")
            out = json.dumps({"is_error": False, "result": "ok", "session_id": sid,
                              "total_cost_usd": 0.01, "num_turns": 1})
            return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")
        finally:
            with self._lock:
                self._active -= 1
                self._per[feature] -= 1


def _seed(conn, settings, project: str, feature: str, tasks: list[tuple[str, list[str]]]) -> None:
    """Ajoute `feature` (+ ses tasks `(slug, depends_on)`) à `project` (déjà créé par le test)."""
    model.add_feature(conn, project_slug=project, slug=feature)
    for slug, deps in tasks:
        model.add_task(conn, feature_ref=f"{project}/{feature}", slug=slug, depends_on=deps)


def _statuses(conn, feature: str) -> dict[str, str]:
    return {r["slug"]: r["status"] for r in conn.execute(
        "SELECT t.slug, t.status FROM tasks t JOIN features f ON t.feature_id = f.id WHERE f.slug = ?",
        (feature,))}


# -- drainage du DAG intra-feature ------------------------------------------------------------------

def test_run_project_drains_intra_feature_dag_in_order(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    _seed(conn, settings, "proj", "feat", [("t1", []), ("t2", ["t1"]), ("t3", ["t2"])])
    r = _Runner()
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r)
    assert summary["dispatched"] == 3 and summary["ok"] == 3 and summary["failed"] == 0
    assert summary["drained"] is True
    assert _statuses(conn, "feat") == {"t1": "done", "t2": "done", "t3": "done"}
    assert [run_["task"] for run_ in summary["runs"]] == ["t1", "t2", "t3"]   # ordre DAG respecté


# -- parallélisme borné inter-features --------------------------------------------------------------

def test_run_project_parallelizes_independent_features_up_to_max(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    for f in ("fa", "fb", "fc"):
        _seed(conn, settings, "proj", f, [("t", [])])
    r = _Runner(delay=0.12)
    summary = orchestrator.run_project(conn, settings, project="proj", max_parallel=2,
                                       git=InternalGit(), runner=r)
    assert summary["dispatched"] == 3 and summary["ok"] == 3 and summary["drained"] is True
    assert r.peak == 2               # a bien parallélisé JUSQU'À la borne… et jamais au-delà (max=2)


# -- mutex par feature (worktree = 1 worker à la fois) ----------------------------------------------

def test_run_project_never_two_workers_on_one_feature(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    # une feature, deux tasks INDÉPENDANTES (les deux READY d'emblée) + budget parallèle large
    _seed(conn, settings, "proj", "feat", [("t-a", []), ("t-b", [])])
    r = _Runner()
    summary = orchestrator.run_project(conn, settings, project="proj", max_parallel=4,
                                       git=InternalGit(), runner=r)
    assert summary["dispatched"] == 2 and summary["ok"] == 2
    assert r.feature_peak == 1       # JAMAIS deux workers concurrents sur la même feature (mutex worktree)
    assert _statuses(conn, "feat") == {"t-a": "done", "t-b": "done"}   # sérialisées mais toutes drainées


# -- tolérance à l'échec ----------------------------------------------------------------------------

def test_run_project_isolates_failure_and_continues(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    _seed(conn, settings, "proj", "good", [("t", [])])
    _seed(conn, settings, "proj", "bad", [("t", [])])
    r = _Runner(fail=("bad",))
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r)
    assert summary["ok"] == 1 and summary["failed"] == 1
    assert summary["failed_features"] == ["bad"] and summary["drained"] is False
    assert _statuses(conn, "good") == {"t": "done"}      # feature saine drainée
    assert _statuses(conn, "bad") == {"t": "todo"}       # KO → revenue todo (re-dispatchable plus tard)


# -- terminaison (pas de boucle infinie sur une NEXT qui échoue toujours) ---------------------------

def test_run_project_terminates_when_next_always_fails(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    _seed(conn, settings, "proj", "stuck", [("t1", []), ("t2", ["t1"])])
    r = _Runner(fail=("stuck",))
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r)
    # t1 échoue → feature exclue → la boucle TERMINE (le test lui-même pendrait sinon). Une seule tentative.
    assert summary["dispatched"] == 1 and summary["ok"] == 0 and summary["failed"] == 1
    assert r.calls == ["stuck"]                          # exactement UN run — pas de re-dispatch en boucle
    assert _statuses(conn, "stuck") == {"t1": "todo", "t2": "todo"}
