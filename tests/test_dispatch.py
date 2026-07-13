"""Tests de la couche dispatch : schéma v2, broker de ports, cycle de worktree (SoT bare réel), gate
no-task-no-dispatch + spawn worker (runner INJECTÉ — aucun vrai `claude`), lecture incrémentale du log."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import schema, store
from cockpit.dispatch import jobs, ports, worker, worktree
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.provision import load_bundle
from cockpit.roadmap import model, prompt


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


# -- schéma courant (v3) ----------------------------------------------------------------------------

def test_schema_current_columns_and_port_table(ctx):
    _, conn = ctx
    assert schema.schema_version(conn) == schema.SCHEMA_VERSION   # base neuve = version courante
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dispatch_jobs)")}
    assert {"session_id", "num_turns", "cost_usd", "wall_s", "engine"} <= cols
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    assert {"kind", "owner"} <= pcols                             # v3
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "port_reservations" in tables


def test_ensure_columns_upgrades_a_v1_db(tmp_path: Path):
    # base v1 minimale (dispatch_jobs sans les colonnes v2) → ensure_columns doit les ajouter
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE dispatch_jobs (id TEXT PRIMARY KEY, task_id TEXT, worktree_path TEXT)")
    conn.execute("PRAGMA user_version = 1")
    schema.ensure_columns(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dispatch_jobs)")}
    assert {"session_id", "num_turns", "cost_usd", "wall_s", "engine"} <= cols


# -- broker de ports --------------------------------------------------------------------------------

def test_ports_reserve_is_idempotent_and_release(ctx):
    _, conn = ctx
    no_probe = None
    a = ports.reserve(conn, project="p", purpose="worktree:f", probe=no_probe)
    b = ports.reserve(conn, project="p", purpose="worktree:f", probe=no_probe)
    assert a["port"] == b["port"]                      # même (projet,purpose) → port STABLE
    other = ports.reserve(conn, project="p", purpose="worktree:g", probe=no_probe)
    assert other["port"] != a["port"]                  # unicité globale (mono-hôte)
    assert ports.release(conn, project="p", purpose="worktree:f")["port"] == a["port"]
    assert ports.release(conn, project="p", purpose="worktree:f") is None   # idempotent


def test_ports_free_port_skips_taken_and_probe(ctx):
    _, conn = ctx
    lo = ports.DEFAULT_RANGE[0]
    # sonde qui déclare lo occupé → free_port saute à lo+1
    assert ports.free_port(conn, probe=lambda port: port == lo) == lo + 1
    ports.reserve(conn, project="p", purpose="a", probe=None)   # prend lo
    assert ports.free_port(conn, probe=None) == lo + 1          # lo réservé → suivant


def test_ports_pool_exhausted(ctx):
    _, conn = ctx
    with pytest.raises(ports.PortPoolExhausted):
        ports.free_port(conn, port_range=(6000, 6000), probe=lambda port: True)


# -- cycle de worktree (SoT bare réel) --------------------------------------------------------------

def _seed_project(conn, settings, *, project="proj", feature="feat", task="schema") -> None:
    registry.create_project(conn, settings, slug=project)   # init_sot seedé (dev+main)
    model.add_feature(conn, project_slug=project, slug=feature)
    model.add_task(conn, feature_ref=f"{project}/{feature}", slug=task)


def test_worktree_reserve_release_audit(ctx):
    settings, conn = ctx
    git = InternalGit()
    _seed_project(conn, settings)
    res = worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    assert res["path"].is_dir() and res["branch"] == "feature/feat"
    assert git.current_branch(res["path"]) == "feature/feat"
    again = worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    assert again["port"] == res["port"]                      # idempotent
    assert worktree.audit(conn, settings) == []             # pas d'orphelin
    # release : worktree retiré + port relâché
    worktree.release(conn, settings, git, project="proj", feature="feat")
    assert not res["path"].exists()
    assert ports.list_reservations(conn) == []
    assert worktree.audit(conn, settings) == []


def test_two_features_reserve_isolated_worktrees_and_ports(ctx):
    """≥2 features en parallèle : chacune son worktree + port + branche distincts, cycle de vie
    indépendant (release de l'une n'affecte pas l'autre). Isolation ressources du modèle feature=mutex."""
    settings, conn = ctx
    git = InternalGit()
    registry.create_project(conn, settings, slug="proj")
    for f in ("feat-a", "feat-b"):
        model.add_feature(conn, project_slug="proj", slug=f)
        model.add_task(conn, feature_ref=f"proj/{f}", slug="t")
    a = worktree.reserve(conn, settings, git, project="proj", feature="feat-a", probe=None)
    b = worktree.reserve(conn, settings, git, project="proj", feature="feat-b", probe=None)
    # les deux coexistent, ressources disjointes
    assert a["path"] != b["path"] and a["path"].is_dir() and b["path"].is_dir()
    assert a["port"] != b["port"]
    assert {a["branch"], b["branch"]} == {"feature/feat-a", "feature/feat-b"}
    assert git.current_branch(a["path"]) == "feature/feat-a"
    assert git.current_branch(b["path"]) == "feature/feat-b"
    assert worktree.audit(conn, settings) == []
    # release de feat-a : feat-b survit intacte, aucun orphelin
    worktree.release(conn, settings, git, project="proj", feature="feat-a")
    assert not a["path"].exists() and b["path"].is_dir()
    assert {r["purpose"] for r in ports.list_reservations(conn)} == {"worktree:feat-b"}
    assert worktree.audit(conn, settings) == []


def test_concurrent_add_worktree_serialized_by_flock(ctx):
    """Deux `add_worktree` concurrents sur le même SoT bare : flock (`git/internal`, #12) sérialise les
    mutations du git-dir partagé → les deux worktrees se créent, aucune corruption `index.lock`/`already
    exists`. Preuve directe du mutex, à la couche git (hors DB, sûr en multi-thread)."""
    settings, conn = ctx
    git = InternalGit()
    registry.create_project(conn, settings, slug="proj")   # seed le SoT bare (dev + main)
    sot = registry.sot_path_for(settings, "proj")
    wt_root = settings.projects_root / "proj" / "worktrees"
    errors: list[Exception] = []

    def _add(name: str) -> None:
        try:
            git.add_worktree(sot, wt_root / name, branch=f"feature/{name}", base="dev")
        except Exception as exc:  # noqa: BLE001 — collecté pour l'assert hors-thread
            errors.append(exc)

    threads = [threading.Thread(target=_add, args=(n,)) for n in ("wa", "wb")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []                                    # flock a sérialisé, aucune collision git-dir
    assert (wt_root / "wa").is_dir() and (wt_root / "wb").is_dir()
    assert git.current_branch(wt_root / "wa") == "feature/wa"
    assert git.current_branch(wt_root / "wb") == "feature/wb"


# -- gate no-task-no-dispatch + spawn (runner injecté) ----------------------------------------------

def _ok_runner(argv, *, cwd, input_text, timeout):
    sid = argv[argv.index("--session-id") + 1]
    out = json.dumps({"is_error": False, "result": "fait", "session_id": sid,
                      "total_cost_usd": 0.02, "num_turns": 2})
    return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")


def _fail_runner(argv, *, cwd, input_text, timeout):
    return run.RunResult(argv=list(argv), returncode=1, stdout="boom", stderr="err")


def test_dispatch_refused_when_feature_has_no_task(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ok_runner)
    assert report["dispatched"] is False and "aucune task" in report["reason"]


def test_dispatch_happy_path_records_job_and_worktree(ctx):
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ok_runner)
    assert report["dispatched"] is True and report["result"]["ok"] is True
    assert report["task"] == "schema"
    job = jobs.get_job(conn, report["job_id"])
    assert job["status"] == "done" and job["num_turns"] == 2 and job["session_id"]
    assert job["port"] and Path(job["worktree_path"]).is_dir()
    # la task passe in_progress (sérialisation) → un 2e dispatch est refusé (gate no-ready)
    task = conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()
    assert task["status"] == "in_progress"
    again = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ok_runner)
    assert again["dispatched"] is False and "READY" in again["reason"]


def test_dispatch_failure_reverts_task_to_todo(ctx):
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_fail_runner)
    assert report["dispatched"] is True and report["result"]["ok"] is False
    job = jobs.get_job(conn, report["job_id"])
    assert job["status"] == "failed"
    task = conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()
    assert task["status"] == "todo"        # re-dispatchable


# -- suivi de log incrémental + normaliseur ---------------------------------------------------------

def test_read_events_incremental_and_partial_line(tmp_path: Path):
    log = tmp_path / "t.jsonl"
    a = json.dumps({"type": "assistant", "timestamp": "T0",
                    "message": {"content": [{"type": "text", "text": "salut"}]}})
    log.write_text(a + "\n", encoding="utf-8")
    first = jobs.read_events(log)
    assert len(first["events"]) == 1 and first["events"][0]["text"] == "salut"
    # ligne partielle (écriture en cours) → non consommée
    with log.open("a", encoding="utf-8") as f:
        f.write('{"type":"assistant","message":{"content":[{"type":"text","text":"partiel"')
    mid = jobs.read_events(log, offset=first["offset"], inode=first["inode"])
    assert mid["events"] == [] and mid["offset"] == first["offset"]
    # la ligne se complète → lue au prochain passage
    with log.open("a", encoding="utf-8") as f:
        f.write("}]}}\n")
    last = jobs.read_events(log, offset=mid["offset"], inode=mid["inode"])
    assert len(last["events"]) == 1 and last["events"][0]["text"] == "partiel"


def test_normalize_line_tool_use_and_result_and_noise():
    tool = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la /tmp"}}]}})
    ev = jobs.normalize_line(tool)
    assert ev["tools"][0] == {"name": "Bash", "input_summary": "ls -la /tmp"}
    res = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "ok", "is_error": False}]}})
    assert jobs.normalize_line(res)["results"][0] == {"ok": True, "summary": "ok"}
    assert jobs.normalize_line("not json") is None
    assert jobs.normalize_line(json.dumps({"type": "queue-operation"})) is None


# -- prompt-builder ---------------------------------------------------------------------------------

def test_build_worker_prompt_uses_project_docs(tmp_path: Path):
    root = tmp_path / "wt"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "design.md").write_text("# Intention\nUn super produit.", encoding="utf-8")
    project = {"slug": "proj", "name": "Proj"}
    feature = {"slug": "feat", "title": "Feature", "branch": "feature/feat"}
    task = {"slug": "schema", "title": "Schéma SQLite", "priority": "P0"}
    text = prompt.build_worker_prompt(project, feature, task, root=root)
    assert "schema — Schéma SQLite (priorité P0)" in text
    assert "worker autonome" in text and "NE touche PAS au cycle git" in text
    assert "Un super produit." in text and "docs/design.md" in text
    # conscience de la carte de doc : le mandat oriente vers `docsmap where`
    assert "docsmap where" in text
    # projet sans docs → fallback explicite, jamais un crash
    bare = prompt.build_worker_prompt(project, feature, task, root=tmp_path / "empty")
    assert "aucun `docs/`" in bare


# -- facette : activation dans la worktree + injection dans le prompt (Phase 3) ---------------------

def _facet_root(tmp_path: Path, facet: str, *, persona: str, method: str, settings: str) -> Path:
    """Un faux worktree portant une facette committée (`.claude/facets/<facet>/…`) + son bundle.toml."""
    root = tmp_path / "wt"
    fdir = root / ".claude" / "facets" / facet
    fdir.mkdir(parents=True)
    (fdir / "PERSONA.md").write_text(persona, encoding="utf-8")
    (fdir / "METHOD.md").write_text(method, encoding="utf-8")
    (fdir / "settings.local.json").write_text(settings, encoding="utf-8")
    cockpit = root / ".cockpit"
    cockpit.mkdir()
    (cockpit / "bundle.toml").write_text(
        f'[bundle]\nproject_type = "x"\nfacets = ["{facet}"]\ndefault_facet = "{facet}"\n', encoding="utf-8")
    return root


def test_reserve_activates_facet_settings_local_gitignored(ctx):
    settings, conn = ctx
    git = InternalGit()
    # projet TYPÉ service-api (le SoT seedé porte la facette backend) + feature facet=backend
    registry.create_project(conn, settings, slug="svc", project_type="service-api")
    model.add_feature(conn, project_slug="svc", slug="api", facet="backend")
    model.add_task(conn, feature_ref="svc/api", slug="t")
    res = worktree.reserve(conn, settings, git, project="svc", feature="api", probe=None)
    activated = res["path"] / ".claude" / "settings.local.json"
    assert activated.is_file()                                   # facette activée dans la worktree
    assert "pytest" in activated.read_text(encoding="utf-8")     # settings de la facette backend
    # la copie activée est GITIGNORÉE (jamais committée par le commit post-run)
    chk = run.run(["git", "-C", str(res["path"]), "check-ignore", ".claude/settings.local.json"])
    assert chk.returncode == 0 and ".claude/settings.local.json" in chk.stdout


def test_build_worker_prompt_injects_facet_persona_method_and_acceptance(tmp_path: Path):
    root = _facet_root(tmp_path, "backend",
                       persona="# Persona — Backend\nTu incarnes un ingénieur backend rigoureux.",
                       method="# Méthode — Backend\n1. Doc-first (anti-boucle).",
                       settings='{"permissions": {"allow": ["Bash(pytest:*)"]}}')
    project = {"slug": "svc", "name": "Svc"}
    feature = {"slug": "api", "title": "API", "branch": "feature/api", "facet": "backend"}
    task = {"slug": "ep", "title": "Endpoint", "priority": "P1",
            "acceptance": "Le endpoint /health répond 200 et un test le couvre."}
    text = prompt.build_worker_prompt(project, feature, task, root=root)
    assert "Facette : backend" in text                          # facette résolue et affichée
    assert "ingénieur backend rigoureux" in text                # persona injectée
    assert "Doc-first (anti-boucle)" in text                    # méthode injectée
    assert "Critères d'acceptation (DoD)" in text and "/health répond 200" in text   # critères requis
    assert prompt.build_worker_prompt(project, feature, task, root=root) == text      # déterministe


def test_build_worker_prompt_failsoft_without_facet_files_or_acceptance(tmp_path: Path):
    # feature.facet=None + pas de bundle.toml → fallback `doc` ; aucune facette committée → pas de persona,
    # pas de crash. Pas d'acceptance → pas de section DoD.
    project = {"slug": "p", "name": "P"}
    feature = {"slug": "f", "title": "F", "branch": "feature/f"}
    task = {"slug": "t", "title": "T", "priority": "P2"}
    text = prompt.build_worker_prompt(project, feature, task, root=tmp_path / "empty")
    assert "Facette : doc" in text                              # fallback
    assert "Critères d'acceptation" not in text                 # pas de DoD si acceptance absente
    assert "worker autonome" in text                            # le mandat reste présent


def test_build_worker_prompt_carries_real_vendored_game_design_persona(tmp_path: Path):
    """DoD P4 (bout-en-bout, contenu RÉEL vendoré) : un dispatch sur une feature `game-design` d'un
    projet browser-game porte la persona/méthode game-design ; `backend` porte une casquette DISTINCTE.
    On seede la worktree depuis `load_bundle("browser-game")` (les vrais `.claude/facets/<f>/`)."""
    root = tmp_path / "wt"
    for rel, content in load_bundle("browser-game").items():    # matérialise le bundle réel sur disque
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
    project = {"slug": "void-runner", "name": "Void Runner"}
    task = {"slug": "econ", "title": "Économie", "priority": "P1"}

    gd = prompt.build_worker_prompt(
        project, {"slug": "balance", "branch": "feature/balance", "facet": "game-design"}, task, root=root)
    assert "Facette : game-design" in gd
    assert "game-designer" in gd and "Décider, pas coder" in gd  # persona + méthode game-design injectées

    be = prompt.build_worker_prompt(
        project, {"slug": "api", "branch": "feature/api", "facet": "backend"}, task, root=root)
    assert "le serveur fait autorité" in be                     # persona backend, casquette distincte
    assert "game-designer" not in be and gd != be               # chaque facette porte SA casquette
