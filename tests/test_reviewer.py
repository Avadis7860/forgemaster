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


# -- trace des runs reviewer (dé-squat + non-runs, F2 du programme trace) ----------------------------

def test_reviewer_run_recorded_as_review_with_true_started_at(ctx):
    """Dé-squat : le run reviewer est journalisé `kind='review'` (engine reviewer-tier1) et son `started_at`
    PRÉCÈDE `ended_at` d'un delta ≥ la durée réelle du run → la régression des 6 ms (started_at posé APRÈS le
    run) ne peut pas revenir. Le worker de task, lui, porte `kind='task'` (défaut)."""
    import time
    from datetime import datetime
    settings, conn = ctx
    _seed(conn, settings)
    _dispatch_worker_and_complete(conn, settings, content="def f():\n    return 1\n")

    def _slow_reviewer(argv, *, cwd, input_text, timeout, env=None):
        time.sleep(0.05)                                    # run réel, non instantané
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": '{"findings":[]}', "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat", runner=_slow_reviewer)
    assert report["reviewed"] is True
    rev = conn.execute(
        "SELECT engine, started_at, ended_at FROM dispatch_jobs WHERE kind='review'").fetchone()
    assert rev is not None and rev["engine"] == "reviewer-tier1"           # run reviewer JOURNALISÉ
    delta = (datetime.fromisoformat(rev["ended_at"])
             - datetime.fromisoformat(rev["started_at"])).total_seconds()
    assert delta >= 0.04                            # started_at AVANT le run de 50 ms (plus le 6 ms menteur)
    assert conn.execute("SELECT 1 FROM dispatch_jobs WHERE kind='task'").fetchone() is not None


def test_reviewer_journals_non_run_on_hold(ctx):
    """Un run qui N'A PAS lieu laisse une ligne : readiness hold (task inachevée) → une entrée `non_runs`
    (kind='review', feature_ref, raison verbatim). Le mapping des raisons existait ; il manquait le puits."""
    settings, conn = ctx
    _seed(conn, settings)                                   # task 'impl' todo → readiness hold
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                                        runner=_reviewer_runner('{"findings":[]}'))
    assert report["reviewed"] is False
    rows = conn.execute("SELECT feature_ref, kind, reason FROM non_runs").fetchall()
    assert len(rows) == 1
    assert rows[0]["feature_ref"] == "proj/feat" and rows[0]["kind"] == "review"
    assert "inachevé" in rows[0]["reason"]                  # la raison verbatim du hold est journalisée


def test_non_run_journal_is_best_effort(ctx):
    """Best-effort, non négociable : un puits de trace cassé (table absente) ne fait JAMAIS échouer l'appel.
    Le skip retourne normalement sa raison ; l'INSERT raté est avalé (warning), pas propagé."""
    settings, conn = ctx
    _seed(conn, settings)
    conn.execute("DROP TABLE non_runs")                    # puits cassé
    conn.commit()
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat",
                                        runner=_reviewer_runner('{"findings":[]}'))
    assert report["reviewed"] is False and "inachevé" in report["reason"]   # le résultat métier survit


# -- P1a : allowlist read-only ÉLARGIE (fin du spin Bash) -------------------------------------------

def test_reviewer_argv_carries_readonly_bash_allowlist(ctx):
    """Le reviewer dispatche avec `--allowedTools = REVIEW_TOOLS` : Read/Grep/Glob + `Bash` git-lecture +
    maps `where`. Fin du spin (avant : `Read,Grep,Glob` sans aucun Bash → tout `git diff`/`codemap` hang en
    headless). Reste read-only : Bash SCOPÉ (jamais de `Bash` nu mutant) et `DENY_DESTRUCTIVE` prime."""
    settings, conn = ctx
    _seed(conn, settings)
    _dispatch_worker_and_complete(conn, settings)
    captured: dict = {}

    def _capture(argv, *, cwd, input_text, timeout, env=None):
        captured["argv"] = list(argv)
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": '{"base":"dev","findings":[]}',
                          "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat", runner=_capture)
    argv = captured["argv"]
    allow = argv[argv.index("--allowedTools") + 1]
    assert allow == reviewer.REVIEW_TOOLS
    assert "Bash(git diff *)" in allow and "Bash(codemap *)" in allow    # git-lecture + maps sanctionnés
    assert "Bash," not in allow and not allow.endswith("Bash")           # jamais de `Bash` nu (read-only)
    assert argv[argv.index("--disallowedTools") + 1] == worker.DENY_DESTRUCTIVE   # bornage prime sur allow


# -- P2 : transcript du reviewer PERSISTÉ (parité worker, zéro duplication) --------------------------

def test_reviewer_default_runner_streams_transcript_and_writes_verdict(ctx, monkeypatch):
    """Sans runner injecté, le reviewer utilise le MÊME primitive streaming que le worker
    (`worker._make_default_runner`) → son `log_path` est ÉCRIT (transcript persisté ; avant, `run.run` ne
    l'écrivait jamais → job review au `log_path` vide) et le pid est enregistré (parité abort). Le verdict
    reste écrit : `run_streaming` rend le stdout complet → parsing du résultat final intact."""
    settings, conn = ctx
    _seed(conn, settings)
    _dispatch_worker_and_complete(conn, settings)
    ndjson = ('{"type":"result","is_error":false,'
              '"result":"{\\"base\\":\\"dev\\",\\"findings\\":[]}","session_id":"rev"}\n')
    spawned: dict = {}

    def _fake_streaming(argv, *, cwd, env=None, timeout=None, input_text=None, out_path=None,
                        new_session=False, on_spawn=None, on_line=None):
        spawned["out_path"] = out_path
        if on_spawn is not None:
            on_spawn(4321)                                  # exerce record_pid (le reviewer devient killable)
        Path(out_path).write_text(ndjson, encoding="utf-8")  # streame le transcript vers log_path
        return run.RunResult(argv=list(argv), returncode=0, stdout=ndjson, stderr="")

    monkeypatch.setattr(run, "run_streaming", _fake_streaming)
    report = reviewer.dispatch_reviewer(conn, settings, feature_ref="proj/feat")   # pas de runner → streaming
    assert report["reviewed"] is True
    row = conn.execute("SELECT log_path, pid FROM dispatch_jobs WHERE kind = 'review'").fetchone()
    assert spawned["out_path"] == row["log_path"]                     # le stream vise bien le log_path du job
    assert Path(row["log_path"]).read_text(encoding="utf-8").strip()  # transcript NON-VIDE (persisté)
    assert row["pid"] == 4321                                         # pid enregistré (parité abort)
    assert review.read_verdict(settings, "proj", "feat") is not None  # verdict écrit (parsing intact)
