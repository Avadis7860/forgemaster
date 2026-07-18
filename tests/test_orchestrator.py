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
def ctx(tmp_path: Path, fake_tools):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)          # base FICHIER : les threads workers rouvrent settings.db_path
    fake_tools(settings)                    # hôte provisionné → le preflight de dispatch passe
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

    def __call__(self, argv, *, cwd, input_text, timeout, env=None):
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


def _new_project(conn, settings, slug: str) -> None:
    """Crée un projet PUIS vide sa roadmap de lancement semée : ces tests pilotent un board CONTRÔLÉ
    (DAG explicite), le socle d'amorçage universel serait du bruit pour la mécanique de drainage."""
    registry.create_project(conn, settings, slug=slug)
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")
    conn.commit()


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
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "feat", [("t1", []), ("t2", ["t1"]), ("t3", ["t2"])])
    r = _Runner()
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r)
    assert summary["dispatched"] == 3 and summary["ok"] == 3 and summary["failed"] == 0
    assert summary["drained"] is True
    assert _statuses(conn, "feat") == {"t1": "done", "t2": "done", "t3": "done"}
    assert [run_["task"] for run_ in summary["runs"]] == ["t1", "t2", "t3"]   # ordre DAG respecté


def test_run_project_surfaces_interactive_task_as_needs_interview(ctx):
    """v12 : une feature dont la next task est `interactive` est TENUE pour le terminal — elle apparaît dans
    `needs_interview`, PAS dans `failed`, aucun worker n'est spawné (runner jamais appelé), et la boucle NE
    spinne PAS. Gate socle (2026-07-18) : tant que le socle n'est pas mergé, la feature de travail headless
    est TENUE (`held_for_socle`), PAS dispatchée — elle branche depuis dev et a besoin du design du socle."""
    settings, conn = ctx
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "work", [("t", [])])                  # feature headless normale
    model.add_feature(conn, project_slug="proj", slug="socle", facet="doc")
    model.add_task(conn, feature_ref="proj/socle", slug="cadrage",
                   acceptance="Intention renseignée.", mode="interactive")
    calls: list = []

    class _SpyRunner:
        def __call__(self, argv, *, cwd, input_text, timeout, env=None):
            calls.append(argv)
            sid = argv[argv.index("--session-id") + 1]
            out = json.dumps({"is_error": False, "result": "ok", "session_id": sid, "num_turns": 1})
            return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=_SpyRunner())
    assert summary["needs_interview"] == ["socle"]           # surfacée, tenue pour l'interview
    assert summary["held_for_socle"] == ["work"]             # gate socle : work tenu (socle non-mergé)
    assert "socle" not in summary["failed_features"] and summary["failed"] == 0
    assert summary["ok"] == 0                                # work NON drainé (socle non-mergé)
    assert calls == []                                       # aucun spawn (socle interactif + work tenu)
    assert _statuses(conn, "socle") == {"cadrage": "todo"}             # jamais in_progress/faux-done
    assert _statuses(conn, "work") == {"t": "todo"}                    # work jamais dispatché


def test_run_project_auto_reconciles_worked_socle_without_interview(ctx, monkeypatch):
    """Auto-heal (bullet 2) : un socle DÉJÀ travaillé (interview a authoré une feature de travail check-verte)
    mais resté OUVERT — sa clôture perdue (PTY tué) — est RÉCONCILIÉ par la pré-passe de `cockpit run`, SANS
    session interactive : socle `done`, jamais tenu en `needs_interview`. Régression live 2026-07-18."""
    settings, conn = ctx
    fake_home = settings.home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))              # trust_workspace / commit isolés
    _new_project(conn, settings, "proj")
    model.add_feature(conn, project_slug="proj", slug="socle", facet="doc")
    model.add_task(conn, feature_ref="proj/socle", slug="cadrage",
                   acceptance="Intention renseignée.", mode="interactive")
    model.add_feature(conn, project_slug="proj", slug="build", facet="code")   # facet valide → check vert
    model.add_task(conn, feature_ref="proj/build", slug="impl", acceptance="Code posé.")
    summary = orchestrator.run_project(conn, settings, project="proj",
                                       runner=_writing_worker(), review_runner=_review_worker())
    assert _statuses(conn, "socle") == {"cadrage": "done"}   # réconcilié sans 2ᵉ interview
    assert "socle" not in summary["needs_interview"]         # jamais tenu au terminal
    assert summary["held_for_socle"] == ["build"]            # gate socle : feature de travail tenue


# -- gate socle : le socle non-mergé est prérequis implicite de toute feature de travail -------------

def _mk_socle(conn, project: str, *, merged: bool, worked: bool = True) -> None:
    """Pose un socle (feature portant une task `interactive`) + statut. `worked` → task `done` (socle clos,
    prêt à merger) ; `merged` → feature `merged` (design sur dev)."""
    model.add_feature(conn, project_slug=project, slug="socle", facet="doc")
    model.add_task(conn, feature_ref=f"{project}/socle", slug="cadrage",
                   acceptance="Intention renseignée.", mode="interactive")
    if worked:
        conn.execute("UPDATE tasks SET status='done' WHERE slug='cadrage'")
    if merged:
        conn.execute("UPDATE features SET status='merged' WHERE slug='socle'")
    conn.commit()


def test_socle_gate_holds_work_until_socle_merged(ctx):
    """Le bug live 2026-07-18 : un socle clos mais NON-mergé laissait le drain partir → les features de
    travail branchaient depuis un `dev` sans design (squelette). Le gate les TIENT (`held_for_socle`), aucun
    spawn, aucun échec — jusqu'au GO humain qui merge le socle."""
    settings, conn = ctx
    _new_project(conn, settings, "proj")
    _mk_socle(conn, "proj", merged=False)                    # socle clos, pas encore mergé
    _seed(conn, settings, "proj", "work", [("t", [])])       # feature de travail headless
    r = _Runner()
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r)
    assert summary["held_for_socle"] == ["work"]             # tenue jusqu'au merge du socle
    assert summary["dispatched"] == 0 and summary["ok"] == 0 and summary["failed"] == 0
    assert "work" not in summary["failed_features"]          # tenue, pas en échec
    assert _statuses(conn, "work") == {"t": "todo"}          # jamais dispatchée


def test_socle_gate_drains_work_once_socle_merged(ctx):
    """Non-régression : socle MERGÉ (design sur dev) → le gate laisse passer, la feature de travail draine
    normalement. Le socle mergé est inerte (exclu du drain)."""
    settings, conn = ctx
    _new_project(conn, settings, "proj")
    _mk_socle(conn, "proj", merged=True)
    _seed(conn, settings, "proj", "work", [("t", [])])
    r = _Runner()
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r)
    assert summary["held_for_socle"] == []                   # rien tenu : socle mergé
    assert summary["ok"] == 1 and summary["drained"] is True
    assert _statuses(conn, "work") == {"t": "done"}          # drainée normalement


def test_no_socle_project_drains_normally(ctx):
    """Un projet SANS socle interactif (mûr / control-plane) n'a pas de gate : drain normal."""
    settings, conn = ctx
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "feat", [("t", [])])       # aucune task interactive → pas de socle
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=_Runner())
    assert summary["held_for_socle"] == [] and summary["ok"] == 1 and summary["drained"] is True


def test_run_feature_holds_work_until_socle_merged(ctx):
    """Gate socle symétrique sur le chemin WEB (`run_feature`) : une feature de travail ciblée sous un socle
    non-mergé est tenue (`held_for_socle`), aucun dispatch."""
    settings, conn = ctx
    _new_project(conn, settings, "proj")
    _mk_socle(conn, "proj", merged=False)
    _seed(conn, settings, "proj", "work", [("t", [])])
    summary = orchestrator.run_feature(conn, settings, project="proj", feature="work",
                                       git=InternalGit(), runner=_Runner())
    assert summary["held_for_socle"] == ["work"] and summary["dispatched"] == 0
    assert _statuses(conn, "work") == {"t": "todo"}


# -- parallélisme borné inter-features --------------------------------------------------------------

def test_run_project_parallelizes_independent_features_up_to_max(ctx):
    settings, conn = ctx
    _new_project(conn, settings, "proj")
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
    _new_project(conn, settings, "proj")
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
    _new_project(conn, settings, "proj")
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
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "stuck", [("t1", []), ("t2", ["t1"])])
    r = _Runner(fail=("stuck",))
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r)
    # t1 échoue → feature exclue → la boucle TERMINE (le test lui-même pendrait sinon). Une seule tentative.
    assert summary["dispatched"] == 1 and summary["ok"] == 0 and summary["failed"] == 1
    assert r.calls == ["stuck"]                          # exactement UN run — pas de re-dispatch en boucle
    assert _statuses(conn, "stuck") == {"t1": "todo", "t2": "todo"}


# -- enforcement du DAG INTER-feature (v10) : design→code -------------------------------------------

def test_run_project_blocks_feature_until_prereq_merged(ctx):
    settings, conn = ctx
    _new_project(conn, settings, "proj")
    # design (aucune dep) puis code (depends_on design) — chacune une task READY d'emblée.
    model.add_feature(conn, project_slug="proj", slug="design")
    model.add_task(conn, feature_ref="proj/design", slug="spec", depends_on=[])
    model.add_feature(conn, project_slug="proj", slug="code", depends_on=["design"])
    model.add_task(conn, feature_ref="proj/code", slug="impl", depends_on=[])
    r = _Runner()
    summary = orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r)
    # design n'est jamais `merged` pendant un run (le merge = gate humain séparé) → code reste BLOCKED_DEPS :
    # SEUL design se dispatche, code jamais. La boucle TERMINE quand même (pas de spin sur feature bloquée).
    assert r.calls == ["design"] and summary["dispatched"] == 1
    assert _statuses(conn, "design") == {"spec": "done"}
    assert _statuses(conn, "code") == {"impl": "todo"}

    # Une fois design MERGÉ (gate humain simulé), code se débloque et se dispatche au run suivant.
    conn.execute("UPDATE features SET status = 'merged' WHERE slug = 'design'")
    conn.commit()
    r2 = _Runner()
    orchestrator.run_project(conn, settings, project="proj", git=InternalGit(), runner=r2)
    assert r2.calls == ["code"]
    assert _statuses(conn, "code") == {"impl": "done"}


# -- CLI `cockpit run` : rapport (smoke, sans worker) -----------------------------------------------

def test_cli_dispatch_reports_empty_roadmap(ctx, capsys, monkeypatch):
    # Projet sans feature dispatchable → run_project ne spawn RIEN (aucun `claude`), imprime un rapport
    # « 0 dispatchée(s) … roadmap drainée » et retourne 0. Prouve le chemin CLI → rapport de bout en bout.
    settings, conn = ctx
    monkeypatch.setattr("cockpit.auth.claude_auth_status",             # auth présente → on teste le rapport
                        lambda *a, **k: {"authenticated": True, "source": "test"})
    _new_project(conn, settings, "empty")
    import argparse
    code = orchestrator.cli_dispatch(settings, argparse.Namespace(
        project="empty", home=None, projects_root=None))
    out = capsys.readouterr().out
    assert code == 0
    assert "run empty : 0 dispatchée(s), 0 ok, 0 échouée(s)" in out and "roadmap drainée" in out


def test_cli_dispatch_refuses_without_claude_auth(ctx, capsys, monkeypatch):
    # Sans auth Claude, `cockpit run` refuse AVANT de spawner (sinon N features échoueraient en série).
    settings, conn = ctx
    monkeypatch.setattr("cockpit.auth.claude_auth_status",
                        lambda *a, **k: {"authenticated": False, "source": None})
    _new_project(conn, settings, "empty")
    import argparse
    code = orchestrator.cli_dispatch(settings, argparse.Namespace(
        project="empty", home=None, projects_root=None))
    assert code == 2 and "claude login" in capsys.readouterr().out


# -- Phase C : finalisation → merge-ready (Tier-0 + reviewer dispatché après le drain) ---------------

def _writing_worker(rel: str = "src/note.sh", content: str = "#!/bin/sh\necho ok\n"):
    """Worker injecté qui ÉCRIT un fichier **code-bearing mais Tier-0 N/A** (`.sh` : aucune toolchain native
    ne le couvre, mais ce N'EST PAS du docs-only → le reviewer Tier-1 est bien exigé/dispatché) puis rend OK.
    Le type isole le chemin **reviewer** ; passer `rel="docs/x.md"` pour tester le skip docs-only."""
    def _run(argv, *, cwd, input_text, timeout, env=None):
        p = Path(cwd) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": "fait", "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")
    return _run


def _review_worker(result: str = '{"findings":[]}'):
    """Reviewer injecté qui rend `result` (findings JSON) comme message final."""
    def _run(argv, *, cwd, input_text, timeout, env=None):
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": result, "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")
    return _run


def test_run_finalizes_complete_feature_to_merge_ready(ctx, monkeypatch):
    """La boucle autonome : après le drain des tasks, une feature complète est FINALISÉE (Tier-0 déterministe
    + **reviewer dispatché**) → **merge-ready** si le gate est vert. Diff doc-only → Tier-0 N/A ; reviewer
    clean → Tier-1 0🔴 frais → gate vert (le merge, lui, reste le GO humain, hors boucle)."""
    settings, conn = ctx
    fake_home = settings.home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))              # trust_workspace n'écrit pas le vrai home
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "feat", [("impl", [])])
    summary = orchestrator.run_project(conn, settings, project="proj",
                                       runner=_writing_worker(), review_runner=_review_worker())
    assert summary["merge_ready"] == ["feat"]
    fin = summary["finalizations"][0]
    assert fin["merge_ready"] is True and fin["review"]["reviewed"] is True and fin["blockers"] == []
    from cockpit.gate import review
    v = review.read_verdict(settings, "proj", "feat")
    assert v is not None and v["counts"]["red"] == 0     # verdict Tier-1 SHA-bound écrit, propre


def test_run_finalizes_docs_only_skips_reviewer(ctx, monkeypatch):
    """Un livrable **docs-only** (prose seule) est finalisé SANS dispatcher de reviewer de code (pas de
    gaspillage de worker — alternative rejetée du finding) et reste **merge-ready** : le gate traite Tier-1
    N/A. Régression du socle-design non-mergeable « Aucune revue Tier-1 » (live 2026-07-18)."""
    settings, conn = ctx
    fake_home = settings.home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "feat", [("impl", [])])

    def _must_not_review(argv, *, cwd, input_text, timeout, env=None):
        raise AssertionError("docs-only ne doit PAS dispatcher de reviewer de code")

    summary = orchestrator.run_project(
        conn, settings, project="proj",
        runner=_writing_worker(rel="docs/design.md", content="# Design\nConcept.\n"),
        review_runner=_must_not_review)
    assert summary["merge_ready"] == ["feat"]                       # merge-ready sans review de code
    fin = summary["finalizations"][0]
    assert fin["merge_ready"] is True and fin["review"]["reviewed"] is False
    assert not any("revue" in b for b in fin["blockers"])           # jamais « aucune revue Tier-1 »


def test_run_feature_not_merge_ready_when_reviewer_flags_red(ctx, monkeypatch):
    """Un 🔴 reviewer cité verbatim → la feature N'est PAS merge-ready (Tier-1 bloque, non-overridé)."""
    settings, conn = ctx
    fake_home = settings.home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "feat", [("impl", [])])
    red = json.dumps({"findings": [{"severity": "🔴", "category": "correctness", "file": "src/note.sh",
                                    "line": 2, "claim": "faux", "evidence": "src/note.sh:2 — echo ok",
                                    "verify_note": "x"}]})
    summary = orchestrator.run_project(conn, settings, project="proj",
                                       runner=_writing_worker(), review_runner=_review_worker(red))
    assert summary["merge_ready"] == []
    fin = summary["finalizations"][0]
    assert fin["merge_ready"] is False and any("Tier-1" in b for b in fin["blockers"])


# -- run_feature : le chemin WEB (draine UNE feature puis finalise → review produite sans clic) ------

def _distinct_writing_worker():
    """Worker injecté écrivant un fichier DISTINCT à chaque appel (compteur de closure) → chaque commit a un
    diff non-vide, y compris au 2ᵉ task d'une feature multi-task (sinon commit vide au 2ᵉ tour)."""
    calls = {"n": 0}
    def _run(argv, *, cwd, input_text, timeout, env=None):
        calls["n"] += 1
        p = Path(cwd) / "src" / f"note-{calls['n']}.sh"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"#!/bin/sh\necho note {calls['n']}\n", encoding="utf-8")
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": "fait", "session_id": sid, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")
    return _run


def test_run_feature_drains_single_task_and_reviews(ctx, monkeypatch):
    """Le chemin WEB symétrisé : `run_feature` draine la task PUIS finalise (Tier-0 + reviewer) → verdict
    Tier-1 produit SANS clic (le défaut : le web ne finalisait jamais → dead-end « attend review »)."""
    settings, conn = ctx
    fake_home = settings.home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "feat", [("impl", [])])
    summary = orchestrator.run_feature(conn, settings, project="proj", feature="feat",
                                       runner=_writing_worker(), review_runner=_review_worker())
    assert summary["merge_ready"] == ["feat"]
    assert _statuses(conn, "feat") == {"impl": "done"}
    from cockpit.gate import review
    v = review.read_verdict(settings, "proj", "feat")
    assert v is not None and v["counts"]["red"] == 0     # verdict Tier-1 SHA-bound écrit, propre


def test_run_feature_advances_multitask_dag_then_reviews(ctx, monkeypatch):
    """Preuve LOAD-BEARING de l'eager-`done` : une feature `[t1, t2 dep t1]` avance sur les DEUX tasks depuis
    le chemin `run_feature` (t2 ne se débloque que si t1 est `done`, cf. resolver) puis produit la review."""
    settings, conn = ctx
    fake_home = settings.home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "feat", [("t1", []), ("t2", ["t1"])])
    summary = orchestrator.run_feature(conn, settings, project="proj", feature="feat",
                                       runner=_distinct_writing_worker(), review_runner=_review_worker())
    assert [r["task"] for r in summary["runs"]] == ["t1", "t2"]       # DAG avancé via eager-`done`
    assert _statuses(conn, "feat") == {"t1": "done", "t2": "done"}
    assert summary["merge_ready"] == ["feat"]


def test_run_feature_stops_and_not_ready_on_worker_failure(ctx):
    """Un worker qui échoue rompt la boucle (task revenue `todo`, pas de spin) → feature NON drainée, non
    finalisée (aucune review sur un travail incomplet), rien de merge-ready."""
    settings, conn = ctx
    _new_project(conn, settings, "proj")
    _seed(conn, settings, "proj", "feat", [("t1", []), ("t2", ["t1"])])
    r = _Runner(fail=("feat",))                          # `fail` clé sur le nom du worktree = slug feature
    summary = orchestrator.run_feature(conn, settings, project="proj", feature="feat", runner=r)
    assert summary["dispatched"] == 1 and summary["failed"] == 1 and summary["drained"] is False
    assert r.calls == ["feat"]                           # exactement UNE tentative — pas de re-dispatch
    assert _statuses(conn, "feat") == {"t1": "todo", "t2": "todo"}
    assert summary["merge_ready"] == []
