"""Tests du review-worker Tier-1 (`dispatch/reviewer.py`) — runner INJECTÉ (aucun vrai `claude`). Couvre la
readiness-gate, l'écriture du verdict SHA-bound, l'idempotence, la garde `evidence⊂diff`, et le parsing des
findings. Le worker de feature écrit un vrai fichier → diff réel `dev...branche` à reviewer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.dispatch import reviewer, worker
from cockpit.gate import review
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


def _seed(conn, settings, *, task="impl"):
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug=task, acceptance="Le module expose f() ; test inclus.")


def _writing_worker(content: str):
    """Runner de worker qui ÉCRIT un fichier dans le worktree → un vrai diff `dev...branche` à reviewer."""
    def _run(argv, *, cwd, input_text, timeout, env=None):
        (Path(cwd) / "feature.py").write_text(content, encoding="utf-8")
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": "fait", "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")
    return _run


def _reviewer_runner(result_text: str):
    """Runner de reviewer qui rend `result_text` (findings JSON) comme message final de `claude -p`."""
    def _run(argv, *, cwd, input_text, timeout, env=None):
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": result_text, "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")
    return _run


def _dispatch_worker_and_complete(conn, settings, content="def f():\n    return broken_call()\n"):
    """Dispatche un worker (écrit `feature.py`) puis marque sa task `done` → feature prête pour la review."""
    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_writing_worker(content))
    conn.execute("UPDATE tasks SET status = 'done' WHERE slug = 'impl'")
    conn.commit()


# -- readiness-gate ---------------------------------------------------------------------------------

def test_reviewer_holds_on_incomplete_work(ctx):
    """Une task non terminée (todo/in_progress) → HOLD honnête, pas de dispatch (pas de faux-positif)."""
    settings, conn = ctx
    _seed(conn, settings)
    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_writing_worker("x = 1\n"))
    # dispatch_next laisse la task `in_progress` (le done est déféré) → inachevé pour la review
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                                        runner=_reviewer_runner('{"findings":[]}'))
    assert report["reviewed"] is False and "inachevé" in report["reason"]


def test_reviewer_holds_on_empty_diff(ctx):
    """Feature sans branche/diff (jamais dispatchée) → hold « jamais dispatchée » (rien à reviewer)."""
    settings, conn = ctx
    _seed(conn, settings)
    conn.execute("UPDATE tasks SET status = 'done' WHERE slug = 'impl'")   # done mais jamais dispatchée
    conn.commit()
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                                        runner=_reviewer_runner('{"findings":[]}'))
    assert report["reviewed"] is False and "jamais dispatchée" in report["reason"]


def test_reviewer_holds_on_blocked_task(ctx):
    """Une task `blocked` n'est ni `todo` ni `in_progress` mais reste **non terminale** (état taskmap
    `BLOCKED`) → HOLD. Régression : l'ancien check `todo`/`in_progress` la laissait passer → review
    prématurée. La readiness suit désormais la terminalité classée par le DAG (une seule autorité)."""
    settings, conn = ctx
    _seed(conn, settings)
    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_writing_worker("x = 1\n"))
    conn.execute("UPDATE tasks SET status = 'blocked' WHERE slug = 'impl'")   # coincée, pas terminale
    conn.commit()
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                                        runner=_reviewer_runner('{"findings":[]}'))
    assert report["reviewed"] is False
    assert "inachevé" in report["reason"] and "BLOCKED" in report["reason"]
    assert review.read_verdict(settings, "proj", "feat") is None            # aucun verdict prématuré


# -- écriture du verdict + garde evidence⊂diff ------------------------------------------------------

def test_reviewer_writes_clean_verdict_on_no_findings(ctx):
    """Travail complet + diff propre (reviewer rend `findings:[]`) → verdict Tier-1 écrit, 0 🔴, frais."""
    settings, conn = ctx
    _seed(conn, settings)
    _dispatch_worker_and_complete(conn, settings)
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                                        runner=_reviewer_runner('{"base":"dev","findings":[]}'))
    assert report["reviewed"] is True and report["counts"]["red"] == 0
    v = review.read_verdict(settings, "proj", "feat")
    assert v is not None and v["counts"]["red"] == 0 and v["reviewer"] == "session"


def test_reviewer_red_finding_survives_when_cited_verbatim(ctx):
    """Un 🔴 dont l'`evidence` cite VERBATIM une ligne ajoutée du diff survit la garde `evidence⊂diff`."""
    settings, conn = ctx
    _seed(conn, settings)
    _dispatch_worker_and_complete(conn, settings, content="def f():\n    return broken_call()\n")
    findings = {"base": "dev", "findings": [{
        "severity": "🔴", "category": "correctness", "file": "feature.py", "line": 2,
        "claim": "broken_call() n'existe pas", "evidence": "feature.py:2 — return broken_call()",
        "verify_note": "réfutation tentée : aucun import → confirmé"}]}
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                                        runner=_reviewer_runner(json.dumps(findings)))
    assert report["reviewed"] is True and report["counts"]["red"] == 1


def test_reviewer_uncited_finding_is_rejected(ctx):
    """Un finding dont la citation n'apparaît PAS dans le diff est rejeté (fail-closed anti-hallucination)."""
    settings, conn = ctx
    _seed(conn, settings)
    _dispatch_worker_and_complete(conn, settings, content="def f():\n    return 1\n")
    findings = {"findings": [{
        "severity": "🔴", "category": "correctness", "file": "feature.py", "line": 9,
        "claim": "inventé", "evidence": "feature.py:9 — cette_ligne_nexiste_pas()", "verify_note": "x"}]}
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                                        runner=_reviewer_runner(json.dumps(findings)))
    assert report["reviewed"] is True and report["counts"]["red"] == 0 and report["rejected"] == 1


# -- idempotence ------------------------------------------------------------------------------------

def test_reviewer_idempotent_skips_fresh_verdict(ctx):
    """Un 2ᵉ dispatch sur le même HEAD (verdict déjà frais) → skip idempotent, aucun re-run."""
    settings, conn = ctx
    _seed(conn, settings)
    _dispatch_worker_and_complete(conn, settings)
    reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                               runner=_reviewer_runner('{"findings":[]}'))
    calls: list = []

    def _spy(argv, *, cwd, input_text, timeout, env=None):
        calls.append(argv)
        out = '{"is_error":false,"result":"{}"}'
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    again = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat", runner=_spy)
    assert again["reviewed"] is False and "idempotent" in again["reason"] and calls == []


# -- parsing des findings (PUR) ---------------------------------------------------------------------

def test_extract_findings_from_preamble_and_fences():
    """Le reviewer peut raisonner avant le JSON final + l'entourer de fences ```json — on retrouve le bloc."""
    txt = ('Je regarde le diff…\nRien de cassé.\n```json\n{"base":"dev","findings":[]}\n```\n')
    assert reviewer._extract_findings(txt) == []
    with_red = 'blabla {"findings":[{"severity":"🔴","file":"a.py","line":1}]}'
    got = reviewer._extract_findings(with_red)
    assert len(got) == 1 and got[0]["severity"] == "🔴"
    assert reviewer._extract_findings("aucun json ici") == []
    assert reviewer._extract_findings(None) == []


def test_build_review_prompt_carries_mandate_diff_and_dod(ctx):
    settings, conn = ctx
    _seed(conn, settings)
    _dispatch_worker_and_complete(conn, settings, content="def f():\n    return 1\n")
    feat = model.resolve_feature(conn, "proj/feat")
    from cockpit.dispatch import worktree as wt_mod
    wt = wt_mod.worktree_path_for(settings, "proj", "feat")
    tasks = [{"slug": "impl", "acceptance": "Le module expose f()."}]
    diff = "diff --git a/feature.py b/feature.py\n+return 1\n"
    prompt = reviewer.build_review_prompt(wt, feat, tasks, diff)
    assert "commission-only" in prompt and "ne modifies RIEN" in prompt
    assert "return 1" in prompt and "Le module expose f()." in prompt      # diff + DoD en cadrage
