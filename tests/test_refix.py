"""test_refix — passe de correction sur gate rouge (`dispatch.refix` + `worker.dispatch_fix`).

Deux niveaux : (1) `dispatch_fix` RÉEL (seed + fake runner) — le worker de correction réserve le worktree,
journalise un job `kind='fix'`, committe (arbre net → no-op propre), sans transition de task. (2)
l'orchestration `dispatch_refix` avec ses collaborateurs LOURDS stubés (evaluate_gate/dispatch_fix/finalize) —
on prouve les gardes (non-rouge / non-refixable / borne / échec) et la dérivation de statut (green/still_red/
exhausted), sans vrai `claude` ni vraie toolchain.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.dispatch import jobs, refix, worker
from cockpit.projects import registry
from cockpit.roadmap import model


@pytest.fixture
def ctx(tmp_path: Path, fake_tools, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    fake_tools(settings)
    yield settings, conn
    conn.close()


def _seed_project(conn, settings, *, project="proj", feature="feat", task="schema") -> None:
    registry.create_project(conn, settings, slug=project)
    model.add_feature(conn, project_slug=project, slug=feature)
    model.add_task(conn, feature_ref=f"{project}/{feature}", slug=task)


def _ok_runner(argv, *, cwd, input_text, timeout, env=None):
    sid = argv[argv.index("--session-id") + 1]
    out = json.dumps({"is_error": False, "result": "corrigé", "session_id": sid, "num_turns": 1})
    return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")


# -- (1) worker.dispatch_fix RÉEL -------------------------------------------------------------------

def test_dispatch_fix_records_fix_job_and_leaves_tasks_untouched(ctx):
    settings, conn = ctx
    _seed_project(conn, settings)
    findings = {"review": {"findings": [{"severity": "🔴", "file": "f.py", "line": 1,
                                         "claim": "bug", "evidence": "f.py:1 — x"}]},
                "toolchain": {"steps": [{"name": "ruff", "cmd": "ruff", "exit_code": 1, "ok": False,
                                         "error": "E501"}]}}
    report = worker.dispatch_fix(conn, settings, feature_ref="proj/feat", findings=findings,
                                 runner=_ok_runner)
    assert report["dispatched"] is True and report["result"]["ok"] is True
    job = jobs.get_job(conn, report["job_id"])
    assert job["kind"] == "fix" and job["status"] == "done"      # journalisé fix, abouti
    feat_id = model.resolve_feature(conn, "proj/feat")["id"]
    assert jobs.count_fix_jobs(conn, feat_id) == 1               # compté pour la borne
    # PAS de transition de task (un fix corrige la feature, pas une task) : la task-ancre reste telle quelle.
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "todo"


def test_dispatch_fix_refused_without_task(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")    # feature sans task → rien à ancrer
    report = worker.dispatch_fix(conn, settings, feature_ref="proj/feat", findings={}, runner=_ok_runner)
    assert report["dispatched"] is False and "aucune task" in report["reason"]


# -- (2) dispatch_refix : gardes + dérivation de statut (collaborateurs stubés) ----------------------

def _decision(*, gate_green: bool, refixable: bool, blockers=None) -> dict:
    return {"gate_green": gate_green, "refixable": refixable, "blockers": list(blockers or []),
            "decision": "hold", "allow": False, "human_go": False}


def _patch(monkeypatch, *, decisions, n_fix=0, fix_result=None):
    """Stub les collaborateurs lourds de `dispatch_refix`. `decisions` = les décisions rendues par les appels
    successifs à `evaluate_gate`. Retourne un `calls` traçant dispatch_fix/finalize."""
    calls = {"fix": 0, "finalize": 0}
    it = iter(decisions)

    def _ev(conn, settings, *, feature_ref, human_go, git=None, **kw):
        return {"decision": next(it), "head_sha": "sha", "feature": {"id": "fid"}}

    def _fix(conn, settings, *, feature_ref, findings, git=None, runner=None):
        calls["fix"] += 1
        return fix_result if fix_result is not None else {"dispatched": True, "result": {"ok": True}}

    def _finalize(conn, settings, project, feature, *, review_runner=None):
        calls["finalize"] += 1
        return {"feature": feature, "merge_ready": False, "blockers": [], "review": None}

    monkeypatch.setattr(refix.merge, "evaluate_gate", _ev)
    monkeypatch.setattr(refix.worker, "dispatch_fix", _fix)
    monkeypatch.setattr(refix.orchestrator, "finalize_feature", _finalize)
    monkeypatch.setattr(refix.jobs, "count_fix_jobs", lambda conn, fid: n_fix)
    return calls


def _run(ctx):
    settings, conn = ctx
    return refix.dispatch_refix(conn, settings, project="proj", feature="feat")


def test_refix_not_red_when_gate_green(ctx, monkeypatch):
    calls = _patch(monkeypatch, decisions=[_decision(gate_green=True, refixable=False)])
    r = _run(ctx)
    assert r["status"] == "not_red" and r["gate_green"] is True
    assert calls["fix"] == 0 and calls["finalize"] == 0        # aucun spawn


def test_refix_not_red_when_never_dispatched(ctx, monkeypatch):
    calls = _patch(monkeypatch, decisions=[None])              # feature sans branche → decision None
    r = _run(ctx)
    assert r["status"] == "not_red" and calls["fix"] == 0


def test_refix_not_refixable_process_guard(ctx, monkeypatch):
    calls = _patch(monkeypatch, decisions=[
        _decision(gate_green=False, refixable=False, blockers=["Tier-1 : aucune revue sur le HEAD"])])
    r = _run(ctx)
    assert r["status"] == "not_refixable" and "reviewer" in r["next_step"]
    assert calls["fix"] == 0                                    # 0 spawn : rien à corriger par un worker


def test_refix_exhausted_before_dispatch(ctx, monkeypatch):
    calls = _patch(monkeypatch, n_fix=refix.MAX_FIX_PASSES,
                   decisions=[_decision(gate_green=False, refixable=True, blockers=["Tier-0 natif"])])
    r = _run(ctx)
    assert r["status"] == "exhausted" and r["fix_pass"] == refix.MAX_FIX_PASSES
    assert calls["fix"] == 0                                    # borne atteinte → aucun spawn


def test_refix_green_after_fix(ctx, monkeypatch):
    calls = _patch(monkeypatch, n_fix=0, decisions=[
        _decision(gate_green=False, refixable=True, blockers=["Tier-0 natif"]),
        _decision(gate_green=True, refixable=False)])          # re-évalué vert
    r = _run(ctx)
    assert r["status"] == "green" and r["gate_green"] is True and r["fix_pass"] == 1
    assert calls["fix"] == 1 and calls["finalize"] == 1


def test_refix_still_red_with_passes_left(ctx, monkeypatch):
    calls = _patch(monkeypatch, n_fix=0, decisions=[
        _decision(gate_green=False, refixable=True, blockers=["Tier-0 natif"]),
        _decision(gate_green=False, refixable=True, blockers=["Tier-0 natif"])])
    r = _run(ctx)
    assert r["status"] == "still_red" and r["fix_pass"] == 1
    assert calls["fix"] == 1 and calls["finalize"] == 1


def test_refix_exhausted_after_last_pass(ctx, monkeypatch):
    calls = _patch(monkeypatch, n_fix=refix.MAX_FIX_PASSES - 1, decisions=[
        _decision(gate_green=False, refixable=True, blockers=["Tier-0 natif"]),
        _decision(gate_green=False, refixable=True, blockers=["Tier-0 natif"])])
    r = _run(ctx)
    assert r["status"] == "exhausted" and r["fix_pass"] == refix.MAX_FIX_PASSES
    assert calls["fix"] == 1


def test_refix_dispatch_failed_skips_finalize(ctx, monkeypatch):
    calls = _patch(monkeypatch, n_fix=0,
                   decisions=[_decision(gate_green=False, refixable=True, blockers=["Tier-0 natif"])],
                   fix_result={"dispatched": True, "result": {"ok": False}, "reason": "timeout"})
    r = _run(ctx)
    assert r["status"] == "dispatch_failed" and r["fix_pass"] == 1
    assert calls["fix"] == 1 and calls["finalize"] == 0        # échec du fix → pas de re-finalise
