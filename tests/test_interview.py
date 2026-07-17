"""test_interview — la commande `cockpit interview` : résolution de la task interactive du socle, lancement
`claude` INTERACTIF (launcher injecté — jamais un vrai `claude`), et clôture VÉRIFIÉE du socle à la sortie."""
from __future__ import annotations

from pathlib import Path

import pytest

from cockpit import interview
from cockpit.config import Settings
from cockpit.db import store
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.roadmap import model


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
