"""test_interview — la commande `cockpit interview` : résolution de la task interactive du socle, lancement
`claude` INTERACTIF (launcher injecté — jamais un vrai `claude`), et clôture VÉRIFIÉE du socle à la sortie."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cockpit import interview
from cockpit.config import Settings
from cockpit.db import store
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.roadmap import model
from cockpit.tools import tools_bin


@pytest.fixture
def ctx(tmp_path: Path, fake_tools, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))          # trust_workspace écrit $HOME/.claude.json — isolé
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    fake_tools(settings)
    yield settings, conn
    conn.close()


def _socle_project(conn, settings, *, project="proj") -> None:
    """Crée un projet PUIS remplace sa graine par un socle CONTRÔLÉ : feature `socle` (facet doc) + task
    `cadrage` INTERACTIVE. L'état d'un projet neuf en attente d'interview (le seed marqué arrive en P3)."""
    registry.create_project(conn, settings, slug=project)
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")
    conn.commit()
    model.add_feature(conn, project_slug=project, slug="socle", facet="doc")
    model.add_task(conn, feature_ref=f"{project}/socle", slug="cadrage",
                   acceptance="Intention renseignée.", mode="interactive")


def test_interview_resolves_launches_interactive_and_completes(ctx):
    """L'interview résout la task interactive, lance `claude` INTERACTIF (argv sans `-p`), et — l'humain ayant
    authoré une feature de travail (roadmap check vert + ≥1 feature) — clôt tout le socle en `done`."""
    settings, conn = ctx
    _socle_project(conn, settings)
    launched: list = []

    def human_launcher(argv, *, cwd, env=None):
        launched.append(argv)
        # L'humain (via la session) remplit la doc puis AUTHORE une feature de travail check-verte.
        model.add_feature(conn, project_slug="proj", slug="build", facet="code")
        model.add_task(conn, feature_ref="proj/build", slug="impl", acceptance="Code posé et testé.")
        return 0

    report = interview.run_interview(conn, settings, project="proj", git=InternalGit(),
                                     launcher=human_launcher)
    assert report["ran"] is True and report["completed"] is True
    assert report["feature"] == "socle" and report["task"] == "cadrage"
    assert launched and launched[0][0] == "claude" and "-p" not in launched[0]   # interactif, jamais headless
    statuses = {r["slug"]: r["status"] for r in conn.execute(
        "SELECT t.slug, t.status FROM tasks t JOIN features f ON t.feature_id=f.id WHERE f.slug='socle'")}
    assert statuses == {"cadrage": "done"}                                        # socle clôturé (verified)


def test_interview_reconcile_only_after_interruption(ctx):
    """Interview interrompue AVANT sa clôture (PTY tué : SIGHUP navigation d'onglet, Ctrl-C, crash) : le
    travail EST produit (feature authorée) mais `verify_and_complete` ne tourne jamais → socle resté ouvert.
    Un 2ᵉ `cockpit interview` RÉCONCILIE sans relancer claude : socle `done`, `reason=reconcile-only`, le
    launcher n'est JAMAIS rappelé. Régression du bug live 2026-07-18."""
    settings, conn = ctx
    _socle_project(conn, settings)

    def crashing_launcher(argv, *, cwd, env=None):
        # L'humain authore une feature de travail (check-verte)… puis le PTY est tué avant la réconciliation.
        model.add_feature(conn, project_slug="proj", slug="build", facet="code")
        model.add_task(conn, feature_ref="proj/build", slug="impl", acceptance="Code posé et testé.")
        raise RuntimeError("PTY tué avant verify_and_complete")

    with pytest.raises(RuntimeError):
        interview.run_interview(conn, settings, project="proj", git=InternalGit(), launcher=crashing_launcher)
    assert conn.execute(                                   # clôture jamais tournée → socle ouvert
        "SELECT status FROM tasks WHERE slug='cadrage'").fetchone()["status"] == "todo"

    def must_not_launch(argv, *, cwd, env=None):
        raise AssertionError("reconcile-only ne doit PAS relancer claude")

    report = interview.run_interview(conn, settings, project="proj", git=InternalGit(),
                                     launcher=must_not_launch)
    assert report["ran"] is True and report["completed"] is True
    assert report["reason"] == "reconcile-only" and report["reconciled"] is True
    assert conn.execute(                                   # socle clôturé sans 2ᵉ interview
        "SELECT status FROM tasks WHERE slug='cadrage'").fetchone()["status"] == "done"


def test_reconcile_socle_noop_when_not_worked(ctx):
    """`reconcile_socle` est un no-op honnête quand rien n'a été produit : socle neuf (aucune feature de
    travail) → rend `None`, ne clôt rien, ne relance rien."""
    settings, conn = ctx
    _socle_project(conn, settings)
    assert interview.reconcile_socle(conn, settings, project="proj", git=InternalGit()) is None
    assert conn.execute("SELECT status FROM tasks WHERE slug='cadrage'").fetchone()["status"] == "todo"


def test_reconcile_socle_report_reconciles_and_reports(ctx):
    """`reconcile_socle_report` (action UI « Valider l'interview & clôturer le socle ») : socle travaillé
    (feature de travail authorée + design.md rempli) → clôt le socle et RAPPORTE en clair (status reconciled,
    N tasks closes, sha du design committé, prochaine étape = drain)."""
    from cockpit.dispatch import worktree
    settings, conn = ctx
    git = InternalGit()
    _socle_project(conn, settings)
    model.add_feature(conn, project_slug="proj", slug="build", facet="code")
    model.add_task(conn, feature_ref="proj/build", slug="impl", acceptance="Code posé et testé.")
    res = worktree.reserve(conn, settings, git, project="proj", feature="socle", probe=None)
    (res["path"] / "design.md").write_text("# Design rempli\n", encoding="utf-8")   # doc éditée à committer

    rep = interview.reconcile_socle_report(conn, settings, project="proj", git=git)
    assert rep["status"] == "reconciled" and rep["completed"] is True
    assert rep["socle_tasks_closed"] == 1                 # la task `cadrage` transitionnée → done
    assert rep["design_sha"] is not None                 # design.md committé (sha remonté)
    assert "cockpit run" in rep["next_step"]
    assert conn.execute("SELECT status FROM tasks WHERE slug='cadrage'").fetchone()["status"] == "done"


def test_reconcile_socle_report_already_closed_then_incomplete_and_no_socle(ctx):
    """Les autres cas rendent TOUJOURS un compte-rendu (jamais muet) : socle déjà clos → `already_closed` ;
    interview inachevée (aucune feature) → `interview_incomplete` ; projet sans socle → `no_socle`."""
    settings, conn = ctx
    git = InternalGit()
    # (a) déjà clôturé : on réconcilie une 1ʳᵉ fois, la 2ᵉ n'a plus rien à faire
    _socle_project(conn, settings)
    model.add_feature(conn, project_slug="proj", slug="build", facet="code")
    model.add_task(conn, feature_ref="proj/build", slug="impl", acceptance="Code.")
    interview.reconcile_socle_report(conn, settings, project="proj", git=git)
    again = interview.reconcile_socle_report(conn, settings, project="proj", git=git)
    assert again["status"] == "already_closed" and again["completed"] is True
    assert "déjà clôturé" in again["next_step"]
    # (b) interview incomplète : socle neuf sans feature de travail
    _socle_project(conn, settings, project="proj2")
    inc = interview.reconcile_socle_report(conn, settings, project="proj2", git=git)
    assert inc["status"] == "interview_incomplete" and inc["completed"] is False
    assert inc["socle_tasks_closed"] == 0 and "Termine l'interview" in inc["next_step"]
    # (c) pas de socle interactif du tout
    registry.create_project(conn, settings, slug="proj3")
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")
    conn.commit()
    model.add_feature(conn, project_slug="proj3", slug="work", facet="code")
    model.add_task(conn, feature_ref="proj3/work", slug="t", acceptance="X.")   # headless
    none_rep = interview.reconcile_socle_report(conn, settings, project="proj3", git=git)
    assert none_rep["status"] == "no_socle" and none_rep["feature"] is None


def test_socle_feature_identifies_socle_durably(ctx):
    """`socle_feature` identifie le socle par sa task `interactive` — marqueur DURABLE, contrairement à
    `resolve_interview` : il tient MÊME quand le socle est clos (task `done`, plus aucune task READY)."""
    settings, conn = ctx
    _socle_project(conn, settings)
    model.add_feature(conn, project_slug="proj", slug="work", facet="code")   # feature de travail (headless)
    model.add_task(conn, feature_ref="proj/work", slug="t", acceptance="X.")
    s = interview.socle_feature(conn, "proj")
    assert s is not None and s["slug"] == "socle"                             # trouvé par la task interactive
    conn.execute("UPDATE tasks SET status='done' WHERE slug='cadrage'")       # socle clos
    conn.commit()
    assert interview.resolve_interview(conn, "proj") is None                  # plus de task READY → None
    assert interview.socle_feature(conn, "proj")["slug"] == "socle"           # mais socle_feature tient


def test_socle_feature_none_when_no_interactive_task(ctx):
    """Projet sans socle interactif (mûr / control-plane) → `socle_feature` rend `None` (rien à gater)."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")
    conn.commit()
    model.add_feature(conn, project_slug="proj", slug="work", facet="code")
    model.add_task(conn, feature_ref="proj/work", slug="t", acceptance="X.")  # headless (défaut)
    assert interview.socle_feature(conn, "proj") is None


def test_interview_incomplete_leaves_socle_open(ctx):
    """Si la session ne produit AUCUNE feature de travail, la vérification échoue (pas de ≥1 feature) → le
    socle reste `todo`, `completed=False`, rien n'est faux-clôturé."""
    settings, conn = ctx
    _socle_project(conn, settings)

    def noop_launcher(argv, *, cwd, env=None):
        return 0                                          # l'humain sort sans rien authorer

    report = interview.run_interview(conn, settings, project="proj", git=InternalGit(),
                                     launcher=noop_launcher)
    assert report["ran"] is True and report["completed"] is False
    assert report["work_feature"] is False
    status = conn.execute("SELECT status FROM tasks WHERE slug='cadrage'").fetchone()["status"]
    assert status == "todo"                                                       # jamais faux-done


def test_interview_env_exposes_cockpit_and_tools_on_path(ctx):
    """La session interview reçoit un env dont le PATH porte `cockpit` (bin du venv courant → authoring de la
    roadmap dans la DB via `cockpit roadmap add-feature`) ET l'outillage (`tools/bin`). Régression du bug live
    2026-07-18 : `env=None` → PATH fragile sans `cockpit`/`node` → session incapable d'authorer."""
    settings, conn = ctx
    _socle_project(conn, settings)
    seen: dict = {}

    def capture_launcher(argv, *, cwd, env=None):
        seen["env"] = env
        return 0

    interview.run_interview(conn, settings, project="proj", git=InternalGit(), launcher=capture_launcher)
    path = (seen["env"] or {}).get("PATH", "")
    assert str(Path(sys.executable).parent) in path          # `cockpit` résout dans la session
    assert str(tools_bin(settings)) in path                  # maps + node résolvent aussi


def test_interview_no_interactive_task_does_not_launch(ctx):
    """Aucune task interactive READY → `ran=False` SANS lancer `claude` (gate no-interactive-no-launch)."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")
    conn.commit()
    model.add_feature(conn, project_slug="proj", slug="work", facet="code")
    model.add_task(conn, feature_ref="proj/work", slug="t", acceptance="X.")     # headless (défaut)
    launched: list = []

    report = interview.run_interview(conn, settings, project="proj", git=InternalGit(),
                                     launcher=lambda *a, **k: launched.append(a) or 0)
    assert report["ran"] is False and launched == []


def test_build_interview_prompt_points_skill_and_renders_acceptance(ctx):
    """Le prompt préparé POINTE le skill semé (méthode dans le bundle) et rend l'`acceptance` verbatim — sans
    logique design-first (nom de doc par type) codée dans le moteur générique."""
    project = {"slug": "proj", "name": "Proj"}
    feature = {"slug": "socle", "title": "Socle"}
    task = {"slug": "cadrage", "acceptance": "docs/design.md § Concept renseigné."}
    prompt = interview.build_interview_prompt(project, feature, task)
    assert "first-session-interview" in prompt                                    # pointe le skill
    assert "roadmap-decompose" in prompt and "cockpit roadmap add-feature proj" in prompt
    assert "docs/design.md § Concept renseigné." in prompt                        # acceptance verbatim
    assert "INTERACTIVE" in prompt
