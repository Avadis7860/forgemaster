"""test_cost — coût token par step→feature→projet (v13).

Trois maillons : (1) `parse_headless_result` EXTRAIT l'usage cumulé + le modèle dominant de l'event `result`
du stream-json ; (2) `record_finish` le PERSISTE (NULL sur un run raté) ; (3) `cost.project_cost` l'AGRÈGE en
réconciliant `total == Σfeatures + nonwork` et `feature == Σsteps + fix`. Fixture stream-json = format RÉEL
capturé (usage + modelUsage), valeurs fictives.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.db import schema, store
from cockpit.dispatch import cost, jobs, worker

_NOW = "2026-07-22T00:00:00Z"

# event `result` portant l'usage cumulé + modelUsage par-modèle (forme réelle, valeurs fictives).
_RESULT_WITH_USAGE = (
    '{"type":"system","subtype":"init","model":"claude-opus-4-8[1m]","session_id":"s1"}\n'
    '{"type":"assistant","message":{"model":"claude-opus-4-8","usage":{"output_tokens":1}}}\n'
    '{"type":"result","subtype":"success","is_error":false,"result":"ok","session_id":"s1",'
    '"num_turns":3,"total_cost_usd":0.05,'
    '"usage":{"input_tokens":10,"output_tokens":20,"cache_read_input_tokens":15000,'
    '"cache_creation_input_tokens":4000},'
    '"modelUsage":{"claude-haiku-4-5":{"costUSD":0.0006},"claude-opus-4-8[1m]":{"costUSD":0.0494}}}\n'
)


@pytest.fixture
def conn(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    c = store.open_db(settings)                 # base neuve migrée à la version courante (v13)
    try:
        yield c
    finally:
        c.close()


def _seed_graph(c: sqlite3.Connection) -> None:
    c.execute("INSERT INTO projects (id,slug,name,sot_path,created_at) VALUES ('p','atlas','Atlas','/x',?)",
              (_NOW,))
    for fid, slug in [("f1", "auth"), ("f2", "billing")]:
        c.execute("INSERT INTO features (id,project_id,slug,title,branch,created_at) "
                  "VALUES (?,?,?,?,?,?)", (fid, "p", slug, slug, f"feature/{slug}", _NOW))
    for tid, fid, slug in [("t1", "f1", "login"), ("t2", "f2", "signup"), ("t3", "f2", "pay")]:
        c.execute("INSERT INTO tasks (id,feature_id,slug,title,created_at) VALUES (?,?,?,?,?)",
                  (tid, fid, slug, slug, _NOW))
    c.commit()


def _u(i: int, o: int, cr: int, cc: int, model: str = "claude-opus-4-8") -> dict:
    return {"input_tokens": i, "output_tokens": o, "cache_read_tokens": cr,
            "cache_creation_tokens": cc, "model": model}


def _finish(c: sqlite3.Connection, *, task_id: str, kind: str, cost_usd: float, usage: dict,
            n: int = 0) -> str:
    """Crée un job par le VRAI chemin `record_start`→`record_finish` (exerce la persistance v13)."""
    jid = jobs.record_start(c, task_id=task_id, worktree="/w",
                            session_id=f"s-{task_id}-{kind}-{n}", kind=kind)
    jobs.record_finish(c, jid, {"ok": True, "cost_usd": cost_usd, "num_turns": 1, **usage})
    return jid


# -- (1) extraction ---------------------------------------------------------------------------------

def test_parse_extracts_cumulative_usage_and_dominant_model():
    p = worker.parse_headless_result(_RESULT_WITH_USAGE, 0)
    assert p["ok"]
    assert (p["input_tokens"], p["output_tokens"]) == (10, 20)
    assert (p["cache_read_tokens"], p["cache_creation_tokens"]) == (15000, 4000)
    # modèle DOMINANT = clé `modelUsage` au `costUSD` max (opus 0.0494 > haiku 0.0006)
    assert p["model"] == "claude-opus-4-8[1m]"
    # le $ reste `total_cost_usd` de Claude — jamais recalculé depuis les tokens
    assert p["cost_usd"] == 0.05


def test_parse_failed_run_has_null_usage_not_zero():
    p = worker.parse_headless_result("", 1)          # rc != 0 → run raté
    assert not p["ok"]
    assert all(p[k] is None for k in
               ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "model"))


def test_parse_usage_without_modelusage_leaves_model_none_and_absent_fields_none():
    nd = ('{"type":"result","is_error":false,"result":"x","session_id":"s","total_cost_usd":0.01,'
          '"usage":{"input_tokens":5,"output_tokens":7}}')
    p = worker.parse_headless_result(nd, 0)
    assert (p["input_tokens"], p["output_tokens"]) == (5, 7)
    assert p["cache_read_tokens"] is None            # absent de `usage` → None (pas un faux 0)
    assert p["model"] is None                        # pas de `modelUsage` → pas de modèle inventé


# -- (2) persistance --------------------------------------------------------------------------------

def test_new_db_is_current_version_with_usage_columns(conn):
    assert schema.schema_version(conn) == schema.SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dispatch_jobs)")}
    assert {"input_tokens", "output_tokens", "cache_read_tokens",
            "cache_creation_tokens", "model"} <= cols


def test_migration_adds_v13_usage_columns_in_place():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE dispatch_jobs (id TEXT PRIMARY KEY, task_id TEXT, worktree_path TEXT)")
    c.execute("PRAGMA user_version = 12")
    schema.ensure_columns(c)                          # migration en place (ALTER ADD COLUMN)
    cols = {r[1] for r in c.execute("PRAGMA table_info(dispatch_jobs)")}
    assert {"input_tokens", "output_tokens", "cache_read_tokens",
            "cache_creation_tokens", "model"} <= cols


def test_record_finish_persists_usage_and_model(conn):
    _seed_graph(conn)
    parsed = worker.parse_headless_result(_RESULT_WITH_USAGE, 0)
    jid = jobs.record_start(conn, task_id="t1", worktree="/w", session_id="s1")
    jobs.record_finish(conn, jid, parsed)
    row = conn.execute("SELECT status, input_tokens, output_tokens, cache_read_tokens, "
                       "cache_creation_tokens, model, cost_usd FROM dispatch_jobs WHERE id=?",
                       (jid,)).fetchone()
    assert row["status"] == "done"
    assert (row["input_tokens"], row["output_tokens"]) == (10, 20)
    assert (row["cache_read_tokens"], row["cache_creation_tokens"]) == (15000, 4000)
    assert row["model"] == "claude-opus-4-8[1m]" and row["cost_usd"] == 0.05


def test_failed_job_persists_null_usage(conn):
    _seed_graph(conn)
    parsed = worker.parse_headless_result("", 1)
    jid = jobs.record_start(conn, task_id="t1", worktree="/w", session_id="sf")
    jobs.record_finish(conn, jid, parsed)
    row = conn.execute("SELECT status, input_tokens, model FROM dispatch_jobs WHERE id=?",
                       (jid,)).fetchone()
    assert row["status"] == "failed" and row["input_tokens"] is None and row["model"] is None


# -- (3) agrégation ---------------------------------------------------------------------------------

def test_project_cost_reconciles_and_attributes(conn):
    _seed_graph(conn)
    # auth/login : 2 jobs task (retry) → sommés en un step
    _finish(conn, task_id="t1", kind="task", cost_usd=0.10, usage=_u(100, 200, 1000, 50), n=1)
    _finish(conn, task_id="t1", kind="task", cost_usd=0.05, usage=_u(50, 100, 500, 25), n=2)
    # billing/signup : 1 task ; un fix (ancré t3=pay) ; une review (overhead)
    _finish(conn, task_id="t2", kind="task", cost_usd=0.20, usage=_u(200, 400, 2000, 100))
    _finish(conn, task_id="t3", kind="fix", cost_usd=0.03, usage=_u(30, 60, 300, 10, "claude-haiku-4-5"))
    _finish(conn, task_id="t2", kind="review", cost_usd=0.08, usage=_u(80, 160, 800, 40))
    # un job task killed SANS usage → compté (n_jobs) mais +0 (jamais un faux zéro synthétisé)
    kj = jobs.record_start(conn, task_id="t1", worktree="/w", session_id="sk")
    jobs.record_finish(conn, kj, {"ok": False}, status="killed")

    d = cost.project_cost(conn, "atlas")
    t = d["total"]
    # réconciliation projet : total == Σfeatures + nonwork
    assert t["cost_usd"] == pytest.approx(sum(f["cost_usd"] for f in d["features"])
                                          + d["nonwork"]["cost_usd"])
    assert t["cost_usd"] == pytest.approx(0.46)
    assert t["tokens"] == 6205                          # somme des 4 types sur les jobs avec usage
    assert t["model"] == "claude-opus-4-8" and t["n_models"] == 2     # opus dominant, haiku présent

    # step : les 2 retries du login sommés + le killed compté à 0
    auth = next(f for f in d["features"] if f["slug"] == "auth")
    login = next(s for s in auth["steps"] if s["task_slug"] == "login")
    assert login["cost_usd"] == pytest.approx(0.15) and login["n_jobs"] == 3

    # fix imputé à la FEATURE, pas à un step ; réconciliation feature == Σsteps + fix
    billing = next(f for f in d["features"] if f["slug"] == "billing")
    assert billing["fix"] is not None and billing["fix"]["cost_usd"] == pytest.approx(0.03)
    assert not any(s["task_slug"] == "pay" for s in billing["steps"])   # le fix n'a pas créé de step
    assert billing["cost_usd"] == pytest.approx(
        sum(s["cost_usd"] for s in billing["steps"]) + billing["fix"]["cost_usd"])

    # review = overhead projet, séparé du travail
    assert d["nonwork"]["cost_usd"] == pytest.approx(0.08) and d["nonwork"]["n_jobs"] == 1


def test_empty_project_is_honest_zero(conn):
    conn.execute("INSERT INTO projects (id,slug,name,sot_path,created_at) VALUES ('pv','void','V','/x',?)",
                 (_NOW,))
    conn.commit()
    d = cost.project_cost(conn, "void")
    assert d["total"]["n_jobs"] == 0 and d["total"]["cost_usd"] == 0.0
    assert d["features"] == [] and d["nonwork"]["n_jobs"] == 0
    assert d["total"]["model"] is None and d["total"]["n_models"] == 0


def test_unknown_project_raises_keyerror(conn):
    with pytest.raises(KeyError):
        cost.project_cost(conn, "nope")


# -- (4) interview de socle (v14) : tokens-only, pas de $ -------------------------------------------

# Transcript d'un `claude` INTERACTIF : PAS d'event `result` (donc pas de $), usage PAR TOUR (message.usage).
# Une ligne non-JSON + une ligne sans usage → doivent être ignorées (fail-soft). Sommes attendues :
# input 5 · output 2500 · cache_read 60000 · cache_creation 5800 → tokens 68305 ; modèle = opus.
_INTERVIEW_TRANSCRIPT = (
    '{"type":"user","message":{"role":"user","content":"salut"}}\n'
    '{"type":"assistant","message":{"model":"claude-opus-4-8","usage":'
    '{"input_tokens":2,"output_tokens":1000,"cache_read_input_tokens":20000,'
    '"cache_creation_input_tokens":5000}}}\n'
    '{"type":"assistant","message":{"model":"claude-opus-4-8","usage":'
    '{"input_tokens":3,"output_tokens":1500,"cache_read_input_tokens":40000,'
    '"cache_creation_input_tokens":800}}}\n'
    'ceci-nest-pas-du-json\n'
)


def _write_transcript(home: Path, session_id: str, body: str) -> None:
    d = home / ".claude" / "projects" / "-home-encoded-cwd"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.jsonl").write_text(body)


def test_record_interview_session_appends_and_dedups(conn):
    from cockpit.projects import registry
    conn.execute("INSERT INTO projects (id,slug,name,sot_path,created_at) VALUES ('p','atlas','A','/x',?)",
                 (_NOW,))
    conn.commit()
    registry.record_interview_session(conn, "atlas", "sid-a")
    registry.record_interview_session(conn, "atlas", "sid-b")
    registry.record_interview_session(conn, "atlas", "sid-a")           # doublon → ignoré
    row = conn.execute("SELECT interview_session_ids FROM projects WHERE slug='atlas'").fetchone()
    import json
    assert json.loads(row[0]) == ["sid-a", "sid-b"]


def test_interview_summed_tokens_only_and_folded_into_total(conn, tmp_path):
    from cockpit.projects import registry
    home = tmp_path / "claude_home"
    _seed_graph(conn)
    # un job de travail (le drain) : $ + tokens
    _finish(conn, task_id="t1", kind="task", cost_usd=0.10, usage=_u(100, 200, 1000, 50))
    # une interview de socle : session persistée + transcript interactif (tokens, pas de $)
    registry.record_interview_session(conn, "atlas", "iv-1")
    _write_transcript(home, "iv-1", _INTERVIEW_TRANSCRIPT)

    d = cost.project_cost(conn, "atlas", home=home)
    iv = d["interview"]
    assert iv is not None
    assert iv["cost_usd"] is None                       # interactif non pricé — jamais un faux $
    assert (iv["input"], iv["output"]) == (5, 2500)
    assert (iv["cache_read"], iv["cache_creation"]) == (60000, 5800)
    assert iv["tokens"] == 68305 and iv["model"] == "claude-opus-4-8" and iv["n_sessions"] == 1

    # les tokens interview GROSSISSENT le total ; le $ total reste celui du drain (interview non pricé)
    assert d["total"]["tokens"] == 1350 + 68305         # job (100+200+1000+50) + interview 68305
    assert d["total"]["cost_usd"] == pytest.approx(0.10)
    # réconciliation v14 : tokens == Σfeatures + nonwork + interview ; $ == Σfeatures + nonwork
    assert d["total"]["tokens"] == (sum(f["tokens"] for f in d["features"])
                                    + d["nonwork"]["tokens"] + iv["tokens"])
    assert d["total"]["cost_usd"] == pytest.approx(
        sum(f["cost_usd"] for f in d["features"]) + d["nonwork"]["cost_usd"])


def test_no_interview_is_none_and_total_unchanged(conn, tmp_path):
    _seed_graph(conn)
    _finish(conn, task_id="t1", kind="task", cost_usd=0.10, usage=_u(100, 200, 1000, 50))
    d = cost.project_cost(conn, "atlas", home=tmp_path / "empty_home")
    assert d["interview"] is None
    assert d["total"]["tokens"] == 1350 and d["total"]["cost_usd"] == pytest.approx(0.10)


def test_interview_session_persisted_but_transcript_missing_is_none(conn, tmp_path):
    from cockpit.projects import registry
    conn.execute("INSERT INTO projects (id,slug,name,sot_path,created_at) VALUES ('p','atlas','A','/x',?)",
                 (_NOW,))
    conn.commit()
    registry.record_interview_session(conn, "atlas", "ghost")          # id persisté, aucun .jsonl
    d = cost.project_cost(conn, "atlas", home=tmp_path / "claude_home")
    assert d["interview"] is None                        # honnête-vide (pas de transcript trouvé)
