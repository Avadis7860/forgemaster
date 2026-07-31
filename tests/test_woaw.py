"""Tests de l'axe esthétique **woaw** (advisory) — le store de verdict (`gate/woaw`), le câblage advisory
dans `compose_merge_decision` (le woaw ne BLOQUE JAMAIS), et le dispatch du juge (`dispatch/woaw`) avec juge +
preview-deploy + screenshot **injectés** (aucun vrai `claude`, aucun conteneur, aucun Playwright)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.dispatch import woaw as woaw_dispatch
from cockpit.dispatch import worker
from cockpit.gate import merge, verify, woaw
from cockpit.projects import registry
from cockpit.roadmap import model

# -- store de verdict (PUR + I/O fichier) -----------------------------------------------------------

def test_build_verdict_counts_and_flat():
    """`build_verdict` dérive les counts par sévérité, capte `flat`/`route`, et n'applique AUCUNE garde diff
    (l'evidence woaw est visuelle) → un finding sans citation de diff est GARDÉ."""
    payload = {"route": "/design-system", "flat": True, "findings": [
        {"severity": "🔴", "principle": "P6", "claim": "vue plate", "evidence": "aucune ombre à l'écran"},
        {"severity": "🟡", "principle": "P2", "claim": "cartes iso"},
        {"severity": "🟣", "principle": "P4", "claim": "rythme"}]}
    v = woaw.build_verdict(payload, sha="abc123", ts="2026-07-30T00:00:00+00:00")
    assert v["counts"] == {"red": 1, "yellow": 1, "purple": 1}
    assert v["flat"] is True and v["route"] == "/design-system" and v["reviewed_sha"] == "abc123"
    assert len(v["findings"]) == 3                              # aucun rejet : pas de garde diff


def test_write_read_status_roundtrip(tmp_path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "proj")
    woaw.write_verdict(settings, "proj", "feat", {"route": "/", "flat": False, "findings": [
        {"severity": "🟡", "principle": "P5", "claim": "wordmark nu"}]}, sha="sha-1")
    st = woaw.status(settings, "proj", "feat", current_sha="sha-1")
    assert st["present"] and st["fresh"] and st["counts"]["yellow"] == 1 and st["flat"] is False
    assert "blocking" not in st                                 # ADVISORY : jamais de clé bloquante


def test_is_fresh_false_on_stale_sha_and_wrong_contract(tmp_path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "proj")
    woaw.write_verdict(settings, "proj", "feat", {"findings": []}, sha="sha-A")
    assert woaw.status(settings, "proj", "feat", current_sha="sha-B")["fresh"] is False   # SHA périmé
    v = woaw.read_verdict(settings, "proj", "feat")
    # vieux contrat → rejeté même si le SHA colle
    assert woaw.is_fresh({**v, "contract_version": "woaw-gate-v0"}, current_sha="sha-A") is False


# -- câblage advisory dans compose_merge_decision (le woaw ne BLOQUE JAMAIS) -------------------------

def _green_inputs():
    """Un gate autrement VERT (Tier-0 propre, Tier-1 frais 0🔴, Tier-1.5 rendu) — pour isoler l'effet woaw."""
    return (
        {"red": 0, "yellow": 0},
        {"present": True, "fresh": True, "counts": {"red": 0, "yellow": 0, "purple": 0}},
        {"present": True, "fresh": True, "blocking": False})


def test_woaw_red_and_flat_never_blocks():
    """Un verdict woaw 2🔴 + seuil de plat sur une feature UI ne touche NI `gate_green` NI `blockers` — il est
    surfacé en reason consultative. C'est la garantie « advisory d'abord » : zéro régression de merge."""
    t0, t1, t15 = _green_inputs()
    d = merge.compose_merge_decision(
        t0, t1, human_go=True, ui_touched=True, t15_status=t15,
        woaw_status={"present": True, "fresh": True, "flat": True, "route": "/design-system",
                     "counts": {"red": 2, "yellow": 1, "purple": 0}})
    assert d["gate_green"] is True and d["allow"] is True       # le woaw NE BLOQUE PAS
    assert not any("woaw" in b.lower() for b in d["blockers"])  # jamais dans les blockers
    reason = next(r for r in d["reasons"] if "woaw" in r.lower())
    assert "2 🔴" in reason and "seuil de plat" in reason and "/design-system" in reason


def test_woaw_absent_surfaces_na_reason_still_green():
    """Feature UI sans verdict woaw frais → reason « N/A (consultatif) », gate reste vert (pas un blocage)."""
    t0, t1, t15 = _green_inputs()
    d = merge.compose_merge_decision(t0, t1, human_go=False, ui_touched=True, t15_status=t15,
                                     woaw_status={"present": False, "fresh": False})
    assert d["gate_green"] is True
    assert any("woaw" in r.lower() and "n/a" in r.lower() for r in d["reasons"])


def test_woaw_silent_when_no_ui():
    """Hors UI, l'axe woaw n'est pas surfacé (N/A muet, comme Tier-1.5) — aucune reason woaw parasite."""
    t0, t1, _ = _green_inputs()
    d = merge.compose_merge_decision(t0, t1, human_go=True, ui_touched=False, woaw_status=None)
    assert not any("woaw" in r.lower() for r in d["reasons"])


# -- parsing de la sortie du juge (PUR) -------------------------------------------------------------

def test_extract_payload_from_preamble_and_fences():
    txt = ('Je lis l\'image…\n```json\n{"route":"/","flat":true,"findings":['
           '{"severity":"🔴","principle":"P1"}]}\n```\n')
    got = woaw_dispatch._extract_payload(txt)
    assert got["flat"] is True and len(got["findings"]) == 1 and got["route"] == "/"
    assert woaw_dispatch._extract_payload("aucun json") == {"findings": []}
    assert woaw_dispatch._extract_payload(None) == {"findings": []}


# -- dispatch du juge (juge + deploy + screenshot INJECTÉS) -----------------------------------------

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


def _ui_writing_worker():
    """Runner de worker qui écrit une page Astro (surface UI) → `has_visual_change` vrai sur le diff."""
    def _run(argv, *, cwd, input_text, timeout, env=None):
        page = Path(cwd) / "web" / "src" / "pages" / "index.astro"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("<main><h1>Avagency</h1></main>\n", encoding="utf-8")
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": "fait", "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")
    return _run


def _seed_ui_feature(conn, settings):
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug="page", acceptance="Rend la home.")
    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ui_writing_worker())
    conn.execute("UPDATE tasks SET status = 'done' WHERE slug = 'page'")
    conn.commit()


def _fake_deploy(conn, settings, *, slug, feature, backend=None):
    wt = f"{settings.projects_root}/proj/worktrees/feat"
    return {"url": "http://127.0.0.1:65535", "workdir": wt}


def _painting_shot_runner(png_bytes=b"\x89PNG\r\n"):
    """Runner screenshot injecté : ÉCRIT le PNG à `payload['screenshot']` (le fichier existe → capture OK)."""
    def _run(payload):
        Path(payload["screenshot"]).write_bytes(png_bytes)
        return run.RunResult(argv=["node"], returncode=0, stdout="{}", stderr="")
    return _run


def _judge_runner(payload_json: str):
    def _run(argv, *, cwd, input_text, timeout, env=None):
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": payload_json, "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")
    return _run


def _dispatch(conn, settings, monkeypatch, judge_json, *, shot=None):
    monkeypatch.setattr(verify, "_wait_http_ready", lambda *a, **k: True)   # pas d'attente réseau réelle
    return woaw_dispatch.dispatch_woaw(
        conn, settings, feature_ref="proj/feat", runner=_judge_runner(judge_json),
        deployer=_fake_deploy, teardowner=lambda *a, **k: None,
        shot_runner=shot or _painting_shot_runner())


def test_dispatch_writes_advisory_verdict_on_happy_path(ctx, monkeypatch):
    """Feature UI complète → capture (injectée) → juge (injecté) rend 1🔴 flat
    → verdict woaw écrit + frais."""
    settings, conn = ctx
    _seed_ui_feature(conn, settings)
    judge = '{"route":"/","flat":true,"findings":[{"severity":"🔴","principle":"P6","claim":"plate"}]}'
    report = _dispatch(conn, settings, monkeypatch, judge)
    assert report["judged"] is True and report["counts"]["red"] == 1 and report["flat"] is True
    v = woaw.read_verdict(settings, "proj", "feat")
    assert v is not None and v["reviewer"] == "woaw-critic" and v["counts"]["red"] == 1
    assert woaw.state_path(settings, "proj", "feat").with_name("woaw-shot.png").is_file()   # screenshot capté


def test_dispatch_holds_on_incomplete_work(ctx, monkeypatch):
    """Task non terminée → readiness hold, aucun verdict (pas de jugement prématuré)."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug="page", acceptance="Rend la home.")
    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ui_writing_worker())
    conn.execute("UPDATE tasks SET status = 'in_progress' WHERE slug = 'page'")   # encore en vol
    conn.commit()
    report = _dispatch(conn, settings, monkeypatch, '{"findings":[]}')
    assert report["judged"] is False and "inachevé" in report["reason"]
    assert woaw.read_verdict(settings, "proj", "feat") is None


def test_dispatch_na_when_no_ui_change(ctx, monkeypatch):
    """Un diff sans surface UI (backend pur) → axe woaw N/A (rien de rendu à juger), pas de verdict."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug="lib", acceptance="Un module.")

    def _py_worker(argv, *, cwd, input_text, timeout, env=None):
        (Path(cwd) / "lib.py").write_text("x = 1\n", encoding="utf-8")
        sid = argv[argv.index("--session-id") + 1]
        return run.RunResult(argv=list(argv), returncode=0,
                             stdout=json.dumps({"is_error": False, "result": "ok", "session_id": sid}),
                             stderr="")
    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_py_worker)
    conn.execute("UPDATE tasks SET status = 'done' WHERE slug = 'lib'")
    conn.commit()
    report = _dispatch(conn, settings, monkeypatch, '{"findings":[]}')
    assert report["judged"] is False and "N/A" in report["reason"]


def test_dispatch_idempotent_skips_fresh_verdict(ctx, monkeypatch):
    """Un 2ᵉ dispatch sur le même HEAD (verdict woaw frais) → skip idempotent, le juge n'est PAS rappelé."""
    settings, conn = ctx
    _seed_ui_feature(conn, settings)
    _dispatch(conn, settings, monkeypatch, '{"flat":false,"findings":[]}')
    calls: list = []

    def _spy(argv, *, cwd, input_text, timeout, env=None):
        calls.append(argv)
        return run.RunResult(
            argv=list(argv), returncode=0, stdout='{"is_error":false,"result":"{}"}', stderr=""
        )

    again = woaw_dispatch.dispatch_woaw(conn, settings, feature_ref="proj/feat", runner=_spy,
                                        deployer=_fake_deploy, teardowner=lambda *a, **k: None,
                                        shot_runner=_painting_shot_runner())
    assert again["judged"] is False and "idempotent" in again["reason"] and calls == []


def test_dispatch_best_effort_when_capture_fails(ctx, monkeypatch):
    """Capture KO (le runner screenshot n'écrit rien → fichier absent) → pas de verdict, `judged=False`, AUCUN
    blocage (advisory). Le juge n'est jamais appelé sans image."""
    settings, conn = ctx
    _seed_ui_feature(conn, settings)

    # n'écrit PAS le PNG → la capture échoue proprement
    def _empty_shot(payload):
        return run.RunResult(argv=["node"], returncode=1, stdout="", stderr="boom")
    called: list = []

    def _judge_spy(argv, *, cwd, input_text, timeout, env=None):
        called.append(argv)
        return run.RunResult(
            argv=list(argv), returncode=0, stdout='{"is_error":false,"result":"{}"}', stderr=""
        )

    monkeypatch.setattr(verify, "_wait_http_ready", lambda *a, **k: True)
    report = woaw_dispatch.dispatch_woaw(conn, settings, feature_ref="proj/feat", runner=_judge_spy,
                                         deployer=_fake_deploy, teardowner=lambda *a, **k: None,
                                         shot_runner=_empty_shot)
    assert report["judged"] is False and "capture" in report["reason"].lower()
    assert called == [] and woaw.read_verdict(settings, "proj", "feat") is None
