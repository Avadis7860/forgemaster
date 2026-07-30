"""Tests de la couche dispatch : schéma v2, broker de ports, cycle de worktree (SoT bare réel), gate
no-task-no-dispatch + spawn worker (runner INJECTÉ — aucun vrai `claude`), lecture incrémentale du log."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import schema, store
from cockpit.dispatch import jobs, ports, worker, worktree
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects import registry
from cockpit.provision import load_bundle
from cockpit.roadmap import model, prompt


@pytest.fixture
def ctx(tmp_path: Path, fake_tools, monkeypatch):
    # HOME isolé : le dispatch marque le workspace trusted dans `$HOME/.claude.json` — on ne touche pas le
    # vrai home, et `tools_env` compose `$HOME/.local/bin` depuis ce home isolé.
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    fake_tools(settings)                    # hôte provisionné → preflight + résolution de `claude` passent
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
    assert worktree.audit(conn, settings, git) == []             # pas d'orphelin
    # release : worktree retiré + port relâché
    worktree.release(conn, settings, git, project="proj", feature="feat")
    assert not res["path"].exists()
    assert ports.list_reservations(conn) == []
    assert worktree.audit(conn, settings, git) == []


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
    assert worktree.audit(conn, settings, git) == []
    # release de feat-a : feat-b survit intacte, aucun orphelin
    worktree.release(conn, settings, git, project="proj", feature="feat-a")
    assert not a["path"].exists() and b["path"].is_dir()
    assert {r["purpose"] for r in ports.list_reservations(conn)} == {"worktree:feat-b"}
    assert worktree.audit(conn, settings, git) == []


def test_reserve_realigns_stale_base(ctx):
    """Un worktree pré-existant (run interrompu) dont la base a divergé de `dev` est RÉALIGNÉ au prochain
    `reserve` (rebase préservant les commits worker), au lieu d'être réutilisé sur une base périmée —
    symétrique de l'anti-stale-base de `run_merge`, mais côté CRÉATION du worktree où code le worker."""
    settings, conn = ctx
    git = InternalGit()
    sot = registry.sot_path_for(settings, "proj")
    _seed_project(conn, settings)                              # proj/feat
    model.add_feature(conn, project_slug="proj", slug="sib")   # sibling pour faire avancer dev
    model.add_task(conn, feature_ref="proj/sib", slug="t")
    ident = resolve_identity("proj", "dev", role="worker")

    # worktree feat + un commit worker (fichier disjoint → rebase trivial)
    res = worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    (res["path"] / "worker.txt").write_text("work\n", encoding="utf-8")
    git.commit_worktree(res["path"], message="feat: worker", identity=ident)
    assert git.is_ancestor(sot, "dev", "feature/feat")         # base fraîche au départ

    # faire avancer `dev` via un sibling mergé → feature/feat devient périmée
    sib = worktree.reserve(conn, settings, git, project="proj", feature="sib", probe=None)
    (sib["path"] / "sib.txt").write_text("sib\n", encoding="utf-8")
    git.commit_worktree(sib["path"], message="sib: worker", identity=ident)
    git.merge_ff(sot, into="dev", source="feature/sib")
    assert not git.is_ancestor(sot, "dev", "feature/feat")     # base périmée (le bug qu'on corrige)

    # reserve à nouveau → réaligne sur dev à jour, SANS perdre le commit worker
    again = worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    assert again["port"] == res["port"]                        # toujours idempotent (port stable)
    assert git.is_ancestor(sot, "dev", "feature/feat")         # RÉALIGNÉ
    assert (res["path"] / "worker.txt").read_text() == "work\n"   # commit worker préservé


def test_audit_flags_stale_base(ctx):
    """Détection PROACTIVE : `audit` flague un worktree dont `dev` n'est plus ancêtre de `feature/<x>` (base
    périmée), sans attendre le prochain `reserve` (qui, lui, réaligne). Complémentaire du fix de reserve
    (`test_reserve_realigns_stale_base`) : ici l'audit voit la divergence AVANT tout re-reserve."""
    settings, conn = ctx
    git = InternalGit()
    sot = registry.sot_path_for(settings, "proj")
    _seed_project(conn, settings)                              # proj/feat
    model.add_feature(conn, project_slug="proj", slug="sib")   # sibling pour faire avancer dev
    model.add_task(conn, feature_ref="proj/sib", slug="t")
    ident = resolve_identity("proj", "dev", role="worker")

    res = worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    (res["path"] / "worker.txt").write_text("work\n", encoding="utf-8")
    git.commit_worktree(res["path"], message="feat: worker", identity=ident)
    # base fraîche → aucune anomalie de base périmée (non-régression)
    assert not [o for o in worktree.audit(conn, settings, git) if o["kind"] == "worktree-base-perimee"]

    # faire avancer `dev` via un sibling mergé → feature/feat devient périmée
    sib = worktree.reserve(conn, settings, git, project="proj", feature="sib", probe=None)
    (sib["path"] / "sib.txt").write_text("sib\n", encoding="utf-8")
    git.commit_worktree(sib["path"], message="sib: worker", identity=ident)
    git.merge_ff(sot, into="dev", source="feature/sib")
    assert not git.is_ancestor(sot, "dev", "feature/feat")     # précondition : base périmée

    # audit signale la base périmée de feat (sib, mergé ff, est à jour → pas flaggé)
    stale = [o for o in worktree.audit(conn, settings, git) if o["kind"] == "worktree-base-perimee"]
    assert stale == [{"kind": "worktree-base-perimee", "project": "proj", "feature": "feat"}]


def test_reserve_stale_base_conflict_surfaces_not_overwrites(ctx):
    """Réalignement d'une base périmée avec conflit RÉEL (même fichier touché des deux côtés) : `reserve`
    lève `GitOpError` (rebase --abort) au lieu d'écraser le travail worker — fail-closed, à re-drainer."""
    settings, conn = ctx
    git = InternalGit()
    sot = registry.sot_path_for(settings, "proj")
    _seed_project(conn, settings)
    model.add_feature(conn, project_slug="proj", slug="sib")
    model.add_task(conn, feature_ref="proj/sib", slug="t")
    ident = resolve_identity("proj", "dev", role="worker")

    res = worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    (res["path"] / "clash.txt").write_text("feat side\n", encoding="utf-8")
    git.commit_worktree(res["path"], message="feat: clash", identity=ident)

    sib = worktree.reserve(conn, settings, git, project="proj", feature="sib", probe=None)
    (sib["path"] / "clash.txt").write_text("dev side\n", encoding="utf-8")   # MÊME fichier → conflit
    git.commit_worktree(sib["path"], message="sib: clash", identity=ident)
    git.merge_ff(sot, into="dev", source="feature/sib")
    assert not git.is_ancestor(sot, "dev", "feature/feat")

    with pytest.raises(GitOpError):
        worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    # rebase --abort → le commit worker survit intact (aucun écrasement)
    assert (res["path"] / "clash.txt").read_text() == "feat side\n"


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

def _ok_runner(argv, *, cwd, input_text, timeout, env=None):
    sid = argv[argv.index("--session-id") + 1]
    out = json.dumps({"is_error": False, "result": "fait", "session_id": sid,
                      "total_cost_usd": 0.02, "num_turns": 2})
    return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")


def _fail_runner(argv, *, cwd, input_text, timeout, env=None):
    return run.RunResult(argv=list(argv), returncode=1, stdout="boom", stderr="err")


def _interrupt_runner(argv, *, cwd, input_text, timeout, env=None):
    # Worker coupé EN VOL par un SIGTERM externe : rc=143 + le marqueur `[Request interrupted by user]`.
    return run.RunResult(argv=list(argv), returncode=143, stdout=_DRAIN_INTERRUPT_LOG, stderr="")


def test_dispatch_refused_when_feature_has_no_task(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ok_runner)
    assert report["dispatched"] is False and "aucune task" in report["reason"]


def test_dispatch_threads_facet_model_into_argv(ctx, monkeypatch):
    """Tiering de modèle bout-en-bout : `dispatch_next` résout le modèle de la facette
    (`resolve_facet_model`) et le thread jusqu'à l'argv du worker → `--model <m>`. Défaut (`None`) ⇒ pas de
    `--model` (comportement historique préservé — les autres tests happy-path le prouvent implicitement)."""
    settings, conn = ctx
    _seed_project(conn, settings)
    monkeypatch.setattr(worker.facet_mod, "resolve_facet_model", lambda root, facet: "sonnet")
    calls: list = []

    def spy_runner(argv, *, cwd, input_text, timeout, env=None):
        calls.append(list(argv))
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": "fait", "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=spy_runner)
    assert report["dispatched"] is True
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_dispatch_interactive_task_routes_to_terminal_without_spawn(ctx):
    """v12 : une next task `mode=interactive` (interview/cadrage) N'EST PAS spawnée en headless — le dispatch
    refuse AVANT tout reserve, surface `needs_terminal`, et laisse la task `todo` (jamais `in_progress` ni
    faux-`done`). Le runner n'est JAMAIS appelé (aucun `claude -p`)."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat", facet="doc")
    model.add_task(conn, feature_ref="proj/feat", slug="cadrage",
                   acceptance="Intention renseignée.", mode="interactive")
    calls: list = []

    def spy_runner(argv, *, cwd, input_text, timeout, env=None):
        calls.append(argv)
        return run.RunResult(argv=list(argv), returncode=0, stdout="{}", stderr="")

    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=spy_runner)
    assert report["dispatched"] is False and report["needs_terminal"] is True
    assert report["reason"] == "interactive" and report["task"] == "cadrage"
    assert calls == []                                          # runner JAMAIS appelé — aucun spawn headless
    task = conn.execute("SELECT status FROM tasks WHERE slug='cadrage'").fetchone()
    assert task["status"] == "todo"                            # ni in_progress ni faux-done
    assert ports.list_reservations(conn) == []                # aucun worktree/port réservé


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


def test_run_worker_external_interrupt_marks_interrupted_not_failed(ctx):
    """D2 : sans abort (sentinelle absente), un worker coupé par un SIGTERM externe (rc=143 + marqueur) est
    finalisé `interrupted` — statut DISTINCT de `failed`. La task revient `todo` (re-dispatchable), la feature
    est TENUE, jamais comptée comme un échec de task (ne poisonne pas l'instrumentation E2)."""
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_interrupt_runner)
    assert report["result"]["interrupted"] is True
    assert jobs.get_job(conn, report["job_id"])["status"] == "interrupted"
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "todo"


def test_run_worker_human_abort_marks_killed_not_interrupted(ctx):
    """D2 (désambiguïsation) : un abort humain tue le worker par SIGTERM → même marqueur/rc qu'un interrupt
    externe. Le seul discriminant est la SENTINELLE (posée AVANT le kill). Sentinelle présente ⇒ le job
    est finalisé `killed` + "aborted by human" (sémantique d'abort préservée), PAS `interrupted`."""
    from cockpit.dispatch import abort
    settings, conn = ctx
    _seed_project(conn, settings)
    abort.request_abort(settings, project="proj")   # pose la sentinelle (0 worker en vol → juste le signal)
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_interrupt_runner)
    job = jobs.get_job(conn, report["job_id"])
    assert job["status"] == "killed" and job["error"] == "aborted by human"


def test_dispatch_injects_tools_bin_on_worker_path(ctx):
    """Le worker est spawné avec un `env` explicite dont le PATH commence par `$COCKPIT_HOME/tools/bin` —
    fin du `env=None` passif : il RÉSOUT codemap/docsmap/frontmap/node/… que sa facette déclare."""
    from cockpit.tools import tools_bin
    settings, conn = ctx
    _seed_project(conn, settings)
    captured: dict = {}

    def capturing_runner(argv, *, cwd, input_text, timeout, env=None):
        captured["env"] = env
        out = json.dumps({"is_error": False, "result": "ok",
                          "session_id": argv[argv.index("--session-id") + 1], "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=capturing_runner)
    assert captured["env"] is not None                                   # env explicite (plus None)
    assert captured["env"]["PATH"].split(":")[0] == str(tools_bin(settings))   # tools/bin EN TÊTE


def test_dispatch_marks_workspace_trusted(ctx):
    """Avant le spawn, le dispatch marque le SoT du projet trusted dans `$HOME/.claude.json` — sinon
    `claude -p` headless ignorerait les `allowedTools` de la facette (workspace non-trusted)."""
    from cockpit.projects.registry import sot_path_for
    settings, conn = ctx
    _seed_project(conn, settings)
    worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ok_runner)
    data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    sot = str(sot_path_for(settings, "proj"))
    assert data["projects"][sot]["hasTrustDialogAccepted"] is True


def test_dispatch_fail_loud_when_claude_absent(ctx, monkeypatch):
    """`claude` (moteur du worker) absent du PATH → dispatch FAIL-LOUD avant le spawn (runner jamais appelé,
    task re-dispatchable), au lieu d'un worker mort-né silencieux (le zombie observé en E2E)."""
    from cockpit.tools import tools_bin
    settings, conn = ctx
    _seed_project(conn, settings)
    (tools_bin(settings) / "claude").unlink()              # hôte sans Claude Code
    monkeypatch.setenv("PATH", "/usr/bin:/bin")            # aucun claude hors tools_bin / ~/.local/bin
    calls: list = []

    def spy_runner(argv, *, cwd, input_text, timeout, env=None):
        calls.append(argv)
        return run.RunResult(argv=list(argv), returncode=0, stdout="{}", stderr="")

    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=spy_runner)
    assert report["dispatched"] is True and not report["result"]["ok"]
    assert "claude" in report["result"]["error"]
    assert calls == []                                     # runner JAMAIS appelé
    task = conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()
    assert task["status"] == "todo"                        # re-dispatchable


# -- P1 : réconciliation des jobs zombies (worker mort / daemon redémarré) --------------------------

def _running_job_on_task(conn, settings, *, task_slug="schema"):
    """Pose un job `running` + sa task `in_progress` — l'état exact d'un worker fauché en plein run."""
    task_id = conn.execute("SELECT id FROM tasks WHERE slug=?", (task_slug,)).fetchone()["id"]
    conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (task_id,))
    conn.commit()
    job_id = jobs.record_start(conn, task_id=task_id, worktree="/tmp/wt", session_id="sess-zombie")
    return job_id, task_id


def test_reconcile_orphans_kills_running_and_reverts_task_and_is_idempotent(ctx):
    """Un `dispatch_jobs.status='running'` au boot est orphelin par construction → `killed`, sa task
    `in_progress`→`todo` (re-dispatchable). Idempotent : un 2ᵉ appel ne trouve plus rien."""
    from cockpit.dispatch import reconcile
    settings, conn = ctx
    _seed_project(conn, settings)
    job_id, _ = _running_job_on_task(conn, settings)
    reconciled = reconcile.reconcile_orphans(conn)
    assert reconciled == [job_id]
    assert jobs.get_job(conn, job_id)["status"] == "killed"
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "todo"
    assert reconcile.reconcile_orphans(conn) == []         # idempotent : plus aucun running


def test_reconcile_preserves_running_job_with_live_pid(ctx):
    """Fix 2026-07-30 : un `cockpit dispatch` CLI VIVANT (process séparé) survit un restart du daemon → son
    job `running` porte un `pid` vivant → `reconcile_orphans` le PRÉSERVE (pas un orphelin). Sans ça il
    serait réapé `killed`, sa task retomberait `todo`, et son `record_finish` deviendrait un no-op silencieux
    → coût/tours/tokens perdus. Sonde `os.getpid()` (le process de test) = vivant."""
    import os

    from cockpit.dispatch import reconcile
    settings, conn = ctx
    _seed_project(conn, settings)
    job_id, _ = _running_job_on_task(conn, settings)
    jobs.record_pid(conn, job_id, os.getpid())             # pid vivant (le process de test lui-même)
    assert reconcile.reconcile_orphans(conn) == []         # worker vivant → préservé, rien réconcilié
    assert jobs.get_job(conn, job_id)["status"] == "running"
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "in_progress"


def test_reconcile_leaves_finished_job_and_its_task_untouched(ctx):
    """La réconciliation ne touche QUE les `running` (WHERE-guard) : un job `done` et la task `in_progress`
    qu'il a légitimement sérialisée restent intacts."""
    from cockpit.dispatch import reconcile
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ok_runner)
    assert reconcile.reconcile_orphans(conn) == []         # aucun running → no-op
    assert jobs.get_job(conn, report["job_id"])["status"] == "done"          # job abouti intact
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "in_progress"


def test_dispatch_finalizes_job_on_unexpected_exception(ctx):
    """Une exception INATTENDUE (≠ ToolPreflightError/RunTimeout) qui échappe au spawn NE laisse PAS un job
    zombie : la garde finalise (killed + task→todo) PUIS re-propage LOUD (jamais avalée)."""
    settings, conn = ctx
    _seed_project(conn, settings)

    def _boom_runner(argv, *, cwd, input_text, timeout, env=None):
        raise RuntimeError("spawn imprévu")

    with pytest.raises(RuntimeError, match="spawn imprévu"):
        worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_boom_runner)
    job = conn.execute("SELECT * FROM dispatch_jobs ORDER BY started_at DESC LIMIT 1").fetchone()
    assert job["status"] == "killed"                       # finalisé, pas zombie
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "todo"


# -- D3 : la réconciliation PRÉFÈRE le résultat (un run fini n'est jamais écrasé killed/NULL) --------

def _running_job_with_log(conn, settings, log_text, *, task_slug="schema", session_id="sess-drain"):
    """Pose un job `running` + task `in_progress`, et écrit `log_text` dans son log streamé (le fichier que la
    réconciliation lit pour décider `done|failed` depuis le verdict, ou `killed` faute de verdict)."""
    task_id = conn.execute("SELECT id FROM tasks WHERE slug=?", (task_slug,)).fetchone()["id"]
    conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (task_id,))
    conn.commit()
    log_path = jobs.dispatch_log_path(settings, session_id)
    log_path.write_text(log_text, encoding="utf-8")
    job_id = jobs.record_start(conn, task_id=task_id, worktree="/tmp/wt", session_id=session_id,
                               log_path=str(log_path))
    return job_id, task_id


# stream-json d'un run RÉUSSI (mirroir du défaut miné D3 : 31 tours, $3.45), verdict terminal `result`.
_DRAIN_SUCCESS_LOG = (
    '{"type":"system","subtype":"init","session_id":"s1"}\n'
    '{"type":"assistant","message":{"content":[{"type":"text","text":"livré"}]}}\n'
    '{"type":"result","subtype":"success","is_error":false,"result":"livré","session_id":"s1",'
    '"num_turns":31,"total_cost_usd":3.45,"duration_ms":740000,'
    '"usage":{"input_tokens":10,"output_tokens":55098,"cache_read_input_tokens":1850898,'
    '"cache_creation_input_tokens":113784}}\n'
)


def test_reconcile_finalizes_done_from_terminal_result(ctx):
    """D3 : un worker qui a ÉMIS son `result` de succès mais dont `record_finish` a manqué (daemon
    mort/redémarré) ne doit PAS être écrasé `killed`/NULL. La réconciliation le finalise `done` + métriques
    réelles DEPUIS son transcript — la trace ne ment jamais sur un succès (sinon poison de l'instrumentation
    d'outcome aval)."""
    from cockpit.dispatch import reconcile
    settings, conn = ctx
    _seed_project(conn, settings)
    job_id, _ = _running_job_with_log(conn, settings, _DRAIN_SUCCESS_LOG)
    assert reconcile.reconcile_orphans(conn) == [job_id]
    job = jobs.get_job(conn, job_id)
    assert job["status"] == "done"                         # jamais killed
    assert job["num_turns"] == 31 and job["cost_usd"] == 3.45
    assert job["wall_s"] == 740.0                          # duration_ms/1000 → wall non-null
    assert job["output_tokens"] == 55098                   # métriques persistées, pas NULL
    assert job["error"] is None


def test_reconcile_finalizes_failed_from_error_result(ctx):
    """Symétrie honnête : si le transcript porte un `result` d'ÉCHEC réel (`is_error:true`), la réconciliation
    finalise `failed` + `error` (pas `killed`) — l'échec du run est journalisé, jamais travesti en kill."""
    from cockpit.dispatch import reconcile
    settings, conn = ctx
    _seed_project(conn, settings)
    log = ('{"type":"result","is_error":true,"subtype":"error_max_turns","session_id":"s",'
           '"num_turns":5,"total_cost_usd":1.2}\n')
    job_id, _ = _running_job_with_log(conn, settings, log)
    assert reconcile.reconcile_orphans(conn) == [job_id]
    job = jobs.get_job(conn, job_id)
    assert job["status"] == "failed" and job["error"]
    assert job["num_turns"] == 5 and job["cost_usd"] == 1.2


def test_reconcile_kills_job_without_terminal_result(ctx):
    """Symétrie : un log qui s'ARRÊTE avant le verdict (worker réellement tué en cours — aucun event `result`)
    reste `killed` + task→`todo`. On ne fabrique jamais un succès à partir d'un transcript coupé."""
    from cockpit.dispatch import reconcile
    settings, conn = ctx
    _seed_project(conn, settings)
    partial = ('{"type":"system","subtype":"init"}\n'
               '{"type":"assistant","message":{"content":[{"type":"text","text":"en cours"}]}}\n')
    job_id, _ = _running_job_with_log(conn, settings, partial)
    assert reconcile.reconcile_orphans(conn) == [job_id]
    assert jobs.get_job(conn, job_id)["status"] == "killed"
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "todo"


def test_reconcile_finalizes_rate_limited_from_terminal_result(ctx):
    """D1×D3 : un orphelin dont le transcript porte un rejet rate-limit est finalisé `rate_limited` (pas
    `killed`, pas `failed`) — la classification survit à la réconciliation d'orphelin (composition des deux
    fixes drain). La task revient `todo` (re-dispatchable après reset)."""
    from cockpit.dispatch import reconcile
    settings, conn = ctx
    _seed_project(conn, settings)
    job_id, _ = _running_job_with_log(conn, settings, _DRAIN_RATE_LIMIT_LOG)
    assert reconcile.reconcile_orphans(conn) == [job_id]
    assert jobs.get_job(conn, job_id)["status"] == "rate_limited"
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "todo"


def test_reconcile_finalizes_interrupted_from_terminal_result(ctx):
    """D2×D3 : un orphelin dont le transcript porte le marqueur d'interruption est finalisé `interrupted` (pas
    `killed`, pas `failed`). La classification survit à la réconciliation d'orphelin ET elle est clé sur le
    MARQUEUR — le transcript est relu SANS rc (returncode=0 par défaut), seul un signal lisible dans le texte
    passe. La task revient `todo` (re-dispatchable)."""
    from cockpit.dispatch import reconcile
    settings, conn = ctx
    _seed_project(conn, settings)
    job_id, _ = _running_job_with_log(conn, settings, _DRAIN_INTERRUPT_LOG)
    assert reconcile.reconcile_orphans(conn) == [job_id]
    assert jobs.get_job(conn, job_id)["status"] == "interrupted"
    assert conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()["status"] == "todo"


# -- P3 : transcript live (stream-json streamé vers log_path) ---------------------------------------

def test_run_streaming_writes_stdout_live_to_out_path(tmp_path: Path):
    """`run_streaming` écrit le stdout **au fil de l'eau** dans `out_path` (le fichier tailé par le pont
    live) ET rend le stdout complet dans le `RunResult` (relu par le parseur du résultat final)."""
    out = tmp_path / "live.jsonl"
    script = ('import sys\n'
              'for i in range(3):\n'
              '    print(\'{"type":"assistant","n":%d}\' % i); sys.stdout.flush()\n')
    res = run.run_streaming([sys.executable, "-c", script], out_path=str(out), timeout=30)
    assert res.returncode == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3 and '"n":2' in lines[-1]           # les 3 lignes ont bien été écrites live
    assert res.stdout.count("assistant") == 3                 # et accumulées dans le RunResult


def test_run_streaming_honors_timeout_even_without_output():
    """Un worker qui pend SANS produire de sortie est tué au timeout (→ RunTimeout) — la garantie
    anti-blocage tient même quand rien n'est streamé (thread lecteur + proc.wait(timeout))."""
    with pytest.raises(run.RunTimeout):
        run.run_streaming([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.4)


def test_run_streaming_aborts_when_on_line_returns_reason():
    """Le prédicat `on_line` coupe le process AVANT le timeout : la 1re raison truthy tue le worker (→ RunSpin
    porteur de la raison). Le script imprime tout puis dormirait 30s — un abort qui marche lève vite (< le
    timeout de 5s) ; un abort cassé retomberait sur RunTimeout (mauvaise exception → le test échoue)."""
    script = ('import sys, time\n'
              'for _ in range(20):\n'
              '    print("SPIN"); sys.stdout.flush()\n'
              'time.sleep(30)\n')
    seen = {"n": 0}

    def _watch(line: str) -> str | None:
        if line.strip() == "SPIN":
            seen["n"] += 1
            if seen["n"] >= 3:
                return "spin: test"
        return None

    with pytest.raises(run.RunSpin) as ei:
        run.run_streaming([sys.executable, "-c", script], timeout=5, on_line=_watch)
    assert "spin: test" in str(ei.value)
    assert isinstance(ei.value, run.RunTimeout)   # RunSpin IS-A RunTimeout → capté par le même except


def test_run_streaming_on_line_returning_none_never_aborts():
    """Non-régression : un `on_line` fourni mais qui ne rend jamais de raison n'interrompt rien — le run se
    termine normalement (aucun RunSpin), le stdout complet est rendu."""
    script = 'print("a"); print("b")'
    res = run.run_streaming([sys.executable, "-c", script], timeout=10, on_line=lambda _l: None)
    assert res.returncode == 0 and res.stdout.count("\n") == 2


def test_build_headless_argv_stream_json_requires_verbose():
    """`claude -p --output-format stream-json` EXIGE `--verbose` ; le défaut `json` ne le pose pas."""
    argv = worker.build_headless_argv(session_id="s", output_format="stream-json")
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert "--verbose" not in worker.build_headless_argv(session_id="s")


def test_build_headless_argv_allowed_tools_override():
    """`allowed_tools`, si fourni, OVERRIDE le choix work/readonly (le reviewer y injecte son allowlist
    élargie) ; sans lui, on retombe sur WRITE_CODE_TOOLS (work) / READONLY_TOOLS (read-only)."""
    over = worker.build_headless_argv(session_id="s", work=False, allowed_tools="Read,Bash(git diff *)")
    assert over[over.index("--allowedTools") + 1] == "Read,Bash(git diff *)"
    assert worker.build_headless_argv(session_id="s", work=True)[
        worker.build_headless_argv(session_id="s", work=True).index("--allowedTools") + 1
    ] == worker.WRITE_CODE_TOOLS
    ro = worker.build_headless_argv(session_id="s", work=False)
    assert ro[ro.index("--allowedTools") + 1] == worker.READONLY_TOOLS


def test_build_headless_argv_permission_mode_per_work():
    """`--permission-mode` posé dans TOUS les cas (headless = zéro interlocuteur) : `acceptEdits` pour
    l'ouvrier (auto-accepte Write/Edit), `dontAsk` pour le reviewer read-only. Un outil hors-allowlist
    tombe en `ask` → sur un workspace trusté (le reviewer appelle `trust_workspace`) → HANG
    jusqu'au timeout (stall silencieux vu sur void-runner). `dontAsk` auto-refuse et ne bloque jamais."""
    w = worker.build_headless_argv(session_id="s", work=True)
    assert w[w.index("--permission-mode") + 1] == worker.WRITE_PERMISSION_MODE == "acceptEdits"
    r = worker.build_headless_argv(session_id="s", work=False)
    assert r[r.index("--permission-mode") + 1] == worker.READONLY_PERMISSION_MODE == "dontAsk"


def test_build_headless_argv_model_optional():
    """`model`, si fourni, pose `--model <m>` (tiering par facette) ; `None` (défaut) l'OMET → le worker
    garde le modèle moteur par défaut (comportement historique préservé, fail-soft)."""
    m = worker.build_headless_argv(session_id="s", work=True, model="sonnet")
    assert m[m.index("--model") + 1] == "sonnet"
    assert "--model" not in worker.build_headless_argv(session_id="s", work=True)


def test_deny_destructive_borders_reliable_forms_not_scratch_rm():
    """DENY rescopé (2026-07-22) : borne les formes que le NOMMAGE rend fiables (push/reset/sudo) et NE
    bloque PLUS `rm` — le blanket `Bash(rm *)` était théâtre (bypass Node) qui interdisait le nettoyage de
    scratch légitime ; les `rm` catastrophiques relèvent du circuit-breaker intrinsèque de `claude`. Le DENY
    est posé dans l'argv worker (deny prime sur allow)."""
    deny = worker.DENY_DESTRUCTIVE
    # bornage conservé : refused-expected
    assert "Bash(git push *)" in deny and "Bash(git reset *)" in deny and "Bash(sudo *)" in deny
    # friction levée : plus aucune règle `rm` → un `rm _preview.tsx` de scratch n'est plus refusé
    assert "rm" not in deny
    argv = worker.build_headless_argv(session_id="s", work=True)
    assert argv[argv.index("--disallowedTools") + 1] == deny


def test_parse_headless_result_extracts_result_event_from_stream_json():
    """stream-json = NDJSON : le parseur retrouve l'événement `result` (verdict final) parmi les lignes
    system/assistant/result, sans se faire piéger par un objet intermédiaire (assistant)."""
    ndjson = "\n".join([
        '{"type":"system","subtype":"init","session_id":"s1"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"OK"}]}}',
        '{"type":"result","subtype":"success","is_error":false,"result":"fini",'
        '"session_id":"s1","total_cost_usd":0.03,"num_turns":4}',
    ])
    parsed = worker.parse_headless_result(ndjson, 0)
    assert parsed["ok"] and parsed["result"] == "fini" and parsed["session_id"] == "s1"
    assert parsed["num_turns"] == 4 and parsed["cost_usd"] == 0.03


def test_parse_headless_result_stream_json_error_event_is_fail_loud():
    nd = '{"type":"result","is_error":true,"subtype":"error_max_turns","session_id":"s"}'
    parsed = worker.parse_headless_result(nd, 0)
    assert not parsed["ok"] and parsed["error"]


# stream-json d'un run rejeté par le plafond 5h de l'org (miroir du défaut miné D1 : rc=1, rejet immédiat).
_DRAIN_RATE_LIMIT_LOG = (
    '{"type":"system","subtype":"init","session_id":"s"}\n'
    '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","rateLimitType":"five_hour",'
    '"overageStatus":"rejected","overageDisabledReason":"org_level_disabled","isUsingOverage":false}}\n'
    '{"type":"assistant","message":{"model":"<synthetic>","content":[]}}\n'
    '{"type":"result","is_error":true,"num_turns":1,"total_cost_usd":0,"session_id":"s"}\n'
)


def test_parse_headless_result_flags_rate_limit_rejection():
    """D1 : un rejet rate-limit (`rate_limit_event status:"rejected"`, ligne NDJSON DISTINCTE du `result`) est
    CLASSÉ `rate_limited` — pas un échec de task. `rc=1` accompagne le rejet (comme le défaut miné) mais la
    classification rate-limit PRIME (sinon un `claude -p rc=1` générique masquerait la vraie cause)."""
    parsed = worker.parse_headless_result(_DRAIN_RATE_LIMIT_LOG, 1)
    assert parsed["rate_limited"] is True
    assert parsed["rate_limit_info"]["rateLimitType"] == "five_hour"
    assert not parsed["ok"] and "rate-limit" in parsed["error"]


def test_parse_no_rate_limit_flag_on_plain_failure():
    """Garde STRICTE : un vrai échec (`result is_error:true`) SANS `rate_limit_event` n'est jamais pris pour
    un rate-limit — il reste un échec classique (`rate_limited=False`), donc la feature est bien exclue
    (`failed`), pas gardée « dispatchable » à tort."""
    nd = '{"type":"result","is_error":true,"subtype":"error_during_execution","session_id":"s","num_turns":1}'
    parsed = worker.parse_headless_result(nd, 0)
    assert parsed["rate_limited"] is False and parsed["rate_limit_info"] is None
    assert not parsed["ok"]


# stream-json d'un run coupé par un SIGTERM externe (miroir du défaut miné D2 : 54 tours, $6.13, marqueur
# `[Request interrupted by user]` puis result is_error, rc=143). Ce n'est PAS un échec de task.
_DRAIN_INTERRUPT_LOG = (
    '{"type":"system","subtype":"init","session_id":"s"}\n'
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{}}]}}\n'
    '{"type":"user","message":{"content":[{"type":"text","text":"[Request interrupted by user]"}]}}\n'
    '{"type":"result","is_error":true,"num_turns":54,"stop_reason":"tool_use","total_cost_usd":6.13,'
    '"session_id":"s"}\n'
)


def test_parse_headless_result_flags_signal_interruption():
    """D2 : un run coupé EN VOL par un SIGTERM externe (marqueur `[Request interrupted by user]` + rc=143) est
    CLASSÉ `interrupted` — pas un échec de task. La classification PRIME sur le verdict rc≠0 (sinon un
    `claude -p rc=143` opaque masquerait la vraie cause et poisonnerait l'instrumentation d'outcome)."""
    parsed = worker.parse_headless_result(_DRAIN_INTERRUPT_LOG, 143)
    assert parsed["interrupted"] is True
    assert not parsed["ok"] and "interrompu" in parsed["error"]


def test_parse_no_interrupt_flag_on_plain_failure():
    """Garde STRICTE : un vrai échec (`result is_error:true`) SANS marqueur d'interruption n'est jamais pris
    pour une interruption — il reste un échec classique (`interrupted=False`), donc la feature est bien exclue
    (`failed`), pas gardée « tenue » à tort."""
    nd = '{"type":"result","is_error":true,"subtype":"error_during_execution","session_id":"s","num_turns":1}'
    parsed = worker.parse_headless_result(nd, 1)
    assert parsed["interrupted"] is False
    assert not parsed["ok"]


def test_dispatch_log_path_is_under_logs_dir_not_claude_transcript(ctx):
    """Le `log_path` du job pointe le log STREAMÉ sous `home/logs/` (que le daemon écrit), pas le transcript
    de session `~/.claude/projects/…` de claude (qu'on n'écrase jamais)."""
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ok_runner)
    job = jobs.get_job(conn, report["job_id"])
    assert job["log_path"].startswith(str(settings.logs_dir))
    assert ".claude/projects" not in job["log_path"]


# -- P1 : récolte du minerai de décisions (worker.result → docs/decisions/) -------------------------

def test_dispatch_harvests_decision_doc_on_success(ctx):
    """Run réussi → le `result` du worker devient `docs/decisions/<date>--<task>.md` dans le worktree,
    embarqué par le commit forge. `_ok_runner` rend result='fait'."""
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_ok_runner)
    wt = Path(jobs.get_job(conn, report["job_id"])["worktree_path"])
    docs = list((wt / "docs" / "decisions").glob("*--schema.md"))
    assert len(docs) == 1 and docs[0].read_text(encoding="utf-8").strip() == "fait"


def test_dispatch_no_decision_doc_on_failure(ctx):
    """Run raté (task revient `todo`) → AUCUN minerai orphelin dans docs/decisions/."""
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=_fail_runner)
    wt = Path(jobs.get_job(conn, report["job_id"])["worktree_path"])
    assert list((wt / "docs" / "decisions").glob("*.md")) == []          # rien écrit
    task = conn.execute("SELECT status FROM tasks WHERE slug='schema'").fetchone()
    assert task["status"] == "todo"


def test_write_decision_doc_writes_body_and_skips_blank(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    doc = worker.write_decision_doc(wt, "my-task", "corps\n## Décisions prises\n- choix x",
                                    date_str="2026-07-14")
    assert doc == wt / "docs" / "decisions" / "2026-07-14--my-task.md"
    assert doc.read_text(encoding="utf-8").endswith("\n") and "## Décisions prises" in doc.read_text("utf-8")
    # blanc ou absent → None, aucun fichier (pas de doc vide)
    assert worker.write_decision_doc(wt, "blank", "   \n ", date_str="2026-07-14") is None
    assert worker.write_decision_doc(wt, "none", None, date_str="2026-07-14") is None
    assert list((wt / "docs" / "decisions").glob("*--blank.md")) == []


def test_write_decision_doc_preamble_prefixes_but_guards_on_result(tmp_path: Path):
    """`preamble` (la passe de fix y met les findings du gate rouge) PRÉFIXE le corps `preamble\\n\\n{result}`
    ; la garde no-op reste sur `result` seul — un preamble sans corps ne fabrique jamais de minerai."""
    wt = tmp_path / "wt"
    wt.mkdir()
    doc = worker.write_decision_doc(wt, "t", "le récit du fix", date_str="2026-07-23",
                                    preamble="## Bloqueurs\n- a.py:12 — null deref")
    body = doc.read_text(encoding="utf-8")
    assert body.startswith("## Bloqueurs\n- a.py:12 — null deref\n\n")
    assert body.rstrip().endswith("le récit du fix")
    # preamble présent mais result blanc/absent → None, aucun fichier (findings seuls ne sont pas du minerai)
    assert worker.write_decision_doc(wt, "b", "  ", date_str="2026-07-23", preamble="## Bloqueurs") is None
    assert worker.write_decision_doc(wt, "n", None, date_str="2026-07-23", preamble="## Bloqueurs") is None


_FIX_FINDINGS = {
    "review": {"findings": [{"severity": "🔴 bloquant", "file": "core.py", "line": 12,
                             "claim": "déréférencement null", "evidence": "x peut être None"}]},
    "toolchain": {"steps": [{"name": "mypy", "cmd": "mypy .", "exit_code": 1, "ok": False,
                             "error": "incompatible type"}]},
}


def test_dispatch_fix_harvests_ore_on_success(ctx):
    """Passe de fix réussie → le savoir de la correction (findings du gate rouge PRÉFIXÉS + récit du worker)
    devient un `docs/decisions/<date>--<feature>-refix-<job>.md` durable, committé avec le fix. Ferme le trou
    C (le fix ne jetait plus son minerai)."""
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_fix(conn, settings, feature_ref="proj/feat", findings=_FIX_FINDINGS,
                                 runner=_ok_runner)
    assert report["dispatched"] and report["result"]["ok"]
    wt = Path(jobs.get_job(conn, report["job_id"])["worktree_path"])
    docs = list((wt / "docs" / "decisions").glob("*--feat-refix-*.md"))
    assert len(docs) == 1
    text = docs[0].read_text(encoding="utf-8")
    assert "findings d'origine" in text                        # le gate rouge d'origine est capturé
    assert "déréférencement null" in text and "mypy" in text   # findings reviewer + Tier-0 rendus
    assert text.rstrip().endswith("fait")                      # le récit du worker suit


def test_dispatch_fix_no_ore_on_failure(ctx):
    """Passe de fix ratée → AUCUN minerai orphelin (symétrie avec le run de task raté)."""
    settings, conn = ctx
    _seed_project(conn, settings)
    report = worker.dispatch_fix(conn, settings, feature_ref="proj/feat", findings=_FIX_FINDINGS,
                                 runner=_fail_runner)
    wt = Path(jobs.get_job(conn, report["job_id"])["worktree_path"])
    assert list((wt / "docs" / "decisions").glob("*.md")) == []


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


# -- spin-guard : détecteur de non-progression ------------------------------------------------------

def _tr_line(text: str) -> str:
    """Une ligne stream-json `tool_result` de summary `text`."""
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": text, "is_error": False}]}})


def _at_line(text: str) -> str:
    """Une ligne stream-json `assistant` porteuse d'un texte de raisonnement."""
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


def test_spin_detector_fires_on_identical_tool_results():
    """La fenêtre pleine de summaries IDENTIQUES et non vides déclenche — à la Nᵉ répétition, pas avant."""
    detect = worker.make_spin_detector(window=3)
    assert detect(_tr_line("win=0/50")) is None            # 1/3
    assert detect(_tr_line("win=0/50")) is None            # 2/3
    reason = detect(_tr_line("win=0/50"))                  # 3/3 → spin
    assert reason is not None and "spin" in reason and "3 tool-results" in reason


def test_spin_detector_silent_on_varied_tool_results():
    """Des summaries qui VARIENT = progression apparente → jamais de déclenchement."""
    detect = worker.make_spin_detector(window=3)
    assert all(detect(_tr_line(f"win={i}/50")) is None for i in range(10))


def test_spin_detector_rearms_on_novel_assistant_text():
    """Un TEXTE d'assistant neuf entre les répétitions réarme (vide la fenêtre) : un worker qui raisonne à
    neuf ne déclenche jamais, même si l'outil rend la même sortie."""
    detect = worker.make_spin_detector(window=3)
    for i in range(10):
        assert detect(_at_line(f"tentative {i}")) is None  # raisonnement neuf → reset
        assert detect(_tr_line("win=0/50")) is None         # même sortie, mais compteur remis à 1


def test_spin_detector_different_summary_breaks_the_streak():
    """Une sortie différente au milieu casse la série : il faut de nouveau `window` identiques d'affilée."""
    detect = worker.make_spin_detector(window=3)
    detect(_tr_line("A"))
    detect(_tr_line("A"))                                    # 2 identiques
    assert detect(_tr_line("B")) is None                    # rupture → pas de spin
    assert detect(_tr_line("A")) is None                    # la série repart : [A,B,A] ≠ tous égaux
    assert detect(_tr_line("A")) is None                    # [B,A,A]
    assert detect(_tr_line("A")) is not None                # [A,A,A] → spin


def test_spin_detector_ignores_empty_and_noise():
    """Summaries vides / lignes de bruit ne déclenchent jamais (garde `summary` non vide)."""
    detect = worker.make_spin_detector(window=2)
    assert detect("not json") is None
    assert detect(_tr_line("")) is None and detect(_tr_line("")) is None   # vides → jamais


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
    # le mandat exige un épilogue de décisions (récolté en minerai par la forge, cf. write_decision_doc)
    assert "## Décisions prises" in text
    # projet sans docs → fallback explicite, jamais un crash
    bare = prompt.build_worker_prompt(project, feature, task, root=tmp_path / "empty")
    assert "aucun `docs/`" in bare


def test_worker_and_fix_prompts_carry_hygiene_and_render_recipe(tmp_path: Path):
    """P1b+P3 : le prompt (worker ET fix) porte la note d'HYGIÈNE partagée — worktree propre au gate (`rm`
    scratch autorisé) + recette de render-preview STATIQUE (`renderToStaticMarkup`, pas de `_preview.tsx` ni
    de dev-server) → fin des tours gaspillés à bricoler un preview."""
    project = {"slug": "proj", "name": "Proj"}
    feature = {"slug": "feat", "title": "Feature", "branch": "feature/feat"}
    task = {"slug": "ui", "title": "Bouton", "priority": "P1"}
    wp = prompt.build_worker_prompt(project, feature, task, root=tmp_path / "wt")
    fp = prompt.build_fix_prompt(project, feature, findings={}, root=tmp_path / "wt")
    for text in (wp, fp):
        assert "PROPRE au gate" in text                       # worktree propre (scratch nettoyé)
        assert "renderToStaticMarkup" in text and "_preview.tsx" in text   # recette render, sans dev-server
        assert "sans navigateur" in text                      # P3-light : aucune dépendance headless


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


# -- capital distillé projet-local : docs/decisions/ relu (cliquet de compounding) -----------------

def _decisions_root(tmp_path: Path, docs: dict[str, str]) -> Path:
    """Un faux worktree portant du minerai déjà distillé (`docs/decisions/<name>.md`)."""
    root = tmp_path / "wt"
    ddir = root / "docs" / "decisions"
    ddir.mkdir(parents=True)
    for name, body in docs.items():
        (ddir / name).write_text(body, encoding="utf-8")
    return root


def test_worker_prompt_reinjects_project_decisions_capital(tmp_path: Path):
    """Trou B — un run RELIT ce qu'un run passé a distillé (`docs/decisions/`), sans dépendre de son
    initiative libre. Preuve : le prompt worker porte les décisions présentes, le plus frais d'abord, chacune
    avec son chemin (le worker lit le fichier entier au besoin)."""
    root = _decisions_root(tmp_path, {
        "2026-07-20--tick-cadence.md": "# Cadence du tick\nLe tick tourne à 20 Hz — verrouillé.",
        "2026-07-22--persistence-drizzle.md": "# Persistance\nDrizzle+SQLite sur volume nommé.",
    })
    project = {"slug": "vr", "name": "VR"}
    feature = {"slug": "f", "title": "F", "branch": "feature/f"}
    task = {"slug": "t", "title": "T", "priority": "P1"}
    text = prompt.build_worker_prompt(project, feature, task, root=root)
    assert "Capital distillé du projet" in text                       # le bloc de relecture est présent
    assert "Le tick tourne à 20 Hz" in text and "Drizzle+SQLite" in text   # les 2 décisions relues
    assert "docs/decisions/2026-07-22--persistence-drizzle.md" in text     # le chemin (lecture complète)
    # récence : le plus frais (2026-07-22) apparaît AVANT le plus ancien (2026-07-20)
    assert text.index("Drizzle+SQLite") < text.index("Le tick tourne à 20 Hz")
    assert prompt.build_worker_prompt(project, feature, task, root=root) == text   # déterministe


def test_fix_prompt_also_reinjects_decisions_capital(tmp_path: Path):
    """La passe de fix relit aussi le capital projet-local (un fix profite des décisions passées)."""
    root = _decisions_root(tmp_path, {"2026-07-22--x.md": "# X\nContrainte porteuse ABC."})
    fp = prompt.build_fix_prompt({"slug": "p", "name": "P"},
                                 {"slug": "f", "branch": "feature/f"}, findings={}, root=root)
    assert "Capital distillé du projet" in fp and "Contrainte porteuse ABC" in fp


def test_decisions_capital_absent_is_failsoft(tmp_path: Path):
    """Projet sans `docs/decisions/` → aucun bloc capital, aucun crash (le prompt se construit)."""
    project = {"slug": "p", "name": "P"}
    feature = {"slug": "f", "title": "F", "branch": "feature/f"}
    task = {"slug": "t", "title": "T", "priority": "P1"}
    text = prompt.build_worker_prompt(project, feature, task, root=tmp_path / "empty")
    assert "Capital distillé du projet" not in text                  # rien à relire → bloc omis
    assert "worker autonome" in text                                 # le reste du prompt tient


def test_decisions_capital_is_budget_bounded_with_pointer(tmp_path: Path):
    """Anti-explosion du prompt (DoD) : beaucoup de décisions volumineuses → borne totale respectée, les
    plus anciennes non-injectées sont pointées (jamais un cap silencieux)."""
    big = "# Grosse décision\n" + ("détail " * 300)   # ~2100 car. > budget à lui seul déborde vite
    docs = {f"2026-07-{d:02d}--dec.md": big for d in range(1, 13)}   # 12 décisions datées
    root = _decisions_root(tmp_path, docs)
    project = {"slug": "p", "name": "P"}
    feature = {"slug": "f", "title": "F", "branch": "feature/f"}
    task = {"slug": "t", "title": "T", "priority": "P1"}
    text = prompt.build_worker_prompt(project, feature, task, root=root)
    block = text.split("## Capital distillé du projet", 1)[1]
    assert len(block) < prompt._DECISIONS_BUDGET + prompt._DECISION_EXCERPT_MAX + 600   # borné
    assert "d'autres décisions plus anciennes" in block              # pointeur explicite (pas de cap muet)
    # récence : la plus récente (07-12) est injectée, une ancienne (07-01) est reléguée au pointeur
    assert "2026-07-12--dec.md" in block and "2026-07-01--dec.md" not in block


# -- cible visuelle projet-local : docs/design/<slug>/brief.md relu (template UI appliqué) ----------

def _design_root(tmp_path: Path, briefs: dict[str, str]) -> Path:
    """Un faux worktree portant des templates UI appliqués (`docs/design/<slug>/brief.md`)."""
    root = tmp_path / "wt"
    for slug, body in briefs.items():
        d = root / "docs" / "design" / slug
        d.mkdir(parents=True)
        (d / "brief.md").write_text(body, encoding="utf-8")
    return root


def test_worker_prompt_reinjects_applied_design_target(tmp_path: Path):
    """Un worker qui attaque l'UI relit la cible visuelle appliquée par le dirigeant
    (`docs/design/*/brief.md`) → il s'en inspire au lieu de coder en aveugle. Preuve : le bloc est présent,
    porte les briefs et leurs chemins, avec la consigne de customisation, déterministe."""
    root = _design_root(tmp_path, {
        "browser-game-spatial": "# Cible visuelle\nHUD glass, deep-space, token-driven.",
        "dashboard-copper": "# Cible visuelle\nCharte cuivre, dense, sobre.",
    })
    project = {"slug": "vr", "name": "VR"}
    feature = {"slug": "f", "title": "F", "branch": "feature/f"}
    task = {"slug": "t", "title": "T", "priority": "P1"}
    text = prompt.build_worker_prompt(project, feature, task, root=root)
    assert "Cible visuelle du projet" in text                        # le bloc de relecture est présent
    assert "HUD glass, deep-space" in text and "Charte cuivre" in text    # les 2 templates appliqués relus
    assert "docs/design/browser-game-spatial/brief.md" in text            # le chemin (frères lus au besoin)
    assert "CUSTOMISE" in text                                            # consigne inspiration+customisation
    assert prompt.build_worker_prompt(project, feature, task, root=root) == text   # déterministe


def test_fix_prompt_also_reinjects_design_target(tmp_path: Path):
    """La passe de fix relit aussi la cible visuelle (un fix d'UI reste ancré sur le template appliqué)."""
    root = _design_root(tmp_path, {"tpl": "# Cible\nIdentité XYZ à reprendre."})
    fp = prompt.build_fix_prompt({"slug": "p", "name": "P"},
                                 {"slug": "f", "branch": "feature/f"}, findings={}, root=root)
    assert "Cible visuelle du projet" in fp and "Identité XYZ" in fp


def test_design_target_absent_is_failsoft(tmp_path: Path):
    """Projet sans `docs/design/` → aucun bloc cible visuelle, aucun crash (le prompt se construit)."""
    project = {"slug": "p", "name": "P"}
    feature = {"slug": "f", "title": "F", "branch": "feature/f"}
    task = {"slug": "t", "title": "T", "priority": "P1"}
    text = prompt.build_worker_prompt(project, feature, task, root=tmp_path / "empty")
    assert "Cible visuelle du projet" not in text                    # rien à relire → bloc omis
    assert "worker autonome" in text                                 # le reste du prompt tient


def test_design_target_is_budget_bounded_with_pointer(tmp_path: Path):
    """Anti-explosion (DoD) : beaucoup de templates appliqués volumineux → borne totale respectée, les
    débordants sont pointés (jamais un cap silencieux). Tri par chemin (slug) : premiers slugs injectés."""
    big = "# Cible\n" + ("token " * 300)                  # ~1800 car. > l'aperçu → déborde vite
    briefs = {f"tpl-{i:02d}": big for i in range(1, 13)}  # 12 templates appliqués
    root = _design_root(tmp_path, briefs)
    project = {"slug": "p", "name": "P"}
    feature = {"slug": "f", "title": "F", "branch": "feature/f"}
    task = {"slug": "t", "title": "T", "priority": "P1"}
    text = prompt.build_worker_prompt(project, feature, task, root=root)
    block = text.split("## Cible visuelle du projet", 1)[1]
    assert len(block) < prompt._DESIGN_BUDGET + prompt._DESIGN_EXCERPT_MAX + 600   # borné
    assert "d'autres templates appliqués" in block                   # pointeur explicite (pas de cap muet)
    # tri par chemin : le 1er slug (tpl-01) injecté, un tardif (tpl-12) relégué au pointeur
    assert "docs/design/tpl-01/brief.md" in block and "tpl-12/brief.md" not in block


def test_record_finish_persists_error_on_failure(ctx):
    """`record_finish` écrit `error` (v11) : le snippet calculé par `parse_headless_result` atterrit en base
    au lieu de mourir dans le retour HTTP → un job `failed` n'est plus muet. Le job porte `kind='task'` (le
    défaut de `record_start` — l'ouvrier ne le passe pas explicitement)."""
    settings, conn = ctx
    _seed_project(conn, settings)
    task_id = conn.execute("SELECT id FROM tasks WHERE slug='schema'").fetchone()["id"]
    job_id = jobs.record_start(conn, task_id=task_id, worktree="/tmp/wt", session_id="s1")
    jobs.record_finish(conn, job_id, {"ok": False, "error": "claude -p rc=1 : boom", "num_turns": 2})
    row = jobs.get_job(conn, job_id)
    assert row["status"] == "failed" and row["error"] == "claude -p rc=1 : boom"
    assert row["kind"] == "task"


def test_record_finish_classifies_rate_limited_not_failed(ctx):
    """D1 : un run rate-limité (`parsed.rate_limited`) est enregistré `rate_limited` — statut DISTINCT de
    `failed` (v15). Sans ça, le plafond 5h de l'org compterait comme un échec de task et poisonnerait
    l'instrumentation d'outcome (E2)."""
    settings, conn = ctx
    _seed_project(conn, settings)
    task_id = conn.execute("SELECT id FROM tasks WHERE slug='schema'").fetchone()["id"]
    job_id = jobs.record_start(conn, task_id=task_id, worktree="/tmp/wt", session_id="s1")
    jobs.record_finish(conn, job_id, {"ok": False, "rate_limited": True, "num_turns": 1, "cost_usd": 0,
                                      "error": "rate-limit five_hour rejeté (overage rejected)"})
    row = jobs.get_job(conn, job_id)
    assert row["status"] == "rate_limited" and row["error"].startswith("rate-limit")


def test_record_finish_classifies_interrupted_not_failed(ctx):
    """D2 : un run interrompu (`parsed.interrupted`) → statut `interrupted`, DISTINCT de `failed`
    (v16). Sans ça, un SIGTERM externe compterait comme un échec de task et poisonnerait l'instrumentation
    d'outcome (E2), exactement comme un rate-limit relu `failed` (D1)."""
    settings, conn = ctx
    _seed_project(conn, settings)
    task_id = conn.execute("SELECT id FROM tasks WHERE slug='schema'").fetchone()["id"]
    job_id = jobs.record_start(conn, task_id=task_id, worktree="/tmp/wt", session_id="s1")
    jobs.record_finish(conn, job_id, {"ok": False, "interrupted": True, "num_turns": 54, "cost_usd": 6.13,
                                      "error": "worker interrompu (Request interrupted, rc=143) — non "
                                               "imputable à la task"})
    row = jobs.get_job(conn, job_id)
    assert row["status"] == "interrupted" and "interrompu" in row["error"]
