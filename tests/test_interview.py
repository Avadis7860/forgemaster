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
