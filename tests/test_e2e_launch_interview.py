"""E2E — la boucle de lancement autonome, fermée par l'interview de 1ʳᵉ session.

Sur un projet neuf **ogame-rogue-like-pve** (seed réel), la boucle complète tient bout-à-bout :
  1. `cockpit run` sur le socle nu → **surface** `needs_interview`, **zéro** headless spawné dessus,
     la task interactive reste `todo` (jamais faux-`done`).
  2. `cockpit interview` → lance `claude` **INTERACTIF** (argv sans `-p`) ; l'humain (launcher injecté)
     remplit `docs/design.md` et author ≥1 feature de travail → tout le socle passe `done` (verified).
  3. `cockpit run` AVANT le merge du socle → la feature de travail est **TENUE** (`held_for_socle`), pas
     drainée : elle branche depuis `dev` et a besoin du design du socle (gate socle 2026-07-18).
  4. **GO humain** : `cockpit merge <socle> --go` → le design (docs-only) atterrit sur `dev`.
  5. `cockpit run` de nouveau → la feature de travail **draine** en HEADLESS depuis un `dev` porteur du
     design (le runner EST appelé).

Launcher/runner injectés : aucun vrai `claude` lancé. Prouve le plumbing déterministe de la boucle ;
le handoff TTY réel (l'humain qui tape dans le terminal) reste un smoke manuel.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cockpit import interview
from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.dispatch import orchestrator
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.roadmap import model

PROJECT = "voidgame"


@pytest.fixture
def ctx(tmp_path: Path, fake_tools):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)          # base FICHIER : les workers rouvrent settings.db_path
    fake_tools(settings)                    # hôte provisionné → le preflight de dispatch passe
    yield settings, conn
    conn.close()


class _Runner:
    """Runner headless synthétique : renvoie un résultat `claude -p` vert, note les features touchées."""
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, argv, *, cwd, input_text, timeout, env=None):
        self.calls.append(Path(cwd).name)
        sid = argv[argv.index("--session-id") + 1]
        out = json.dumps({"is_error": False, "result": "ok", "session_id": sid,
                          "total_cost_usd": 0.01, "num_turns": 1})
        return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")


def _socle(conn) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        """SELECT t.slug, t.status, t.mode FROM tasks t
           JOIN features f ON t.feature_id=f.id JOIN projects p ON f.project_id=p.id
           WHERE p.slug=? AND f.slug LIKE 'socle%' ORDER BY t.rowid""", (PROJECT,)).fetchall()
    return {r["slug"]: (r["status"], r["mode"]) for r in rows}


def _features(conn) -> list[str]:
    return [f["slug"] for f in model.list_features(conn, project_slug=PROJECT)]


def test_launch_loop_closes_via_first_session_interview(ctx):
    settings, conn = ctx

    # 0 — projet neuf ogame-rogue-like-pve : socle-design nu, task `interview` interactive au seed.
    registry.create_project(conn, settings, slug=PROJECT, project_type="ogame-rogue-like-pve")
    assert _socle(conn)["interview"] == ("todo", "interactive")
    assert _features(conn) == ["socle-design"]                       # roadmap figée au socle

    # 1 — cockpit run sur le socle nu : surface needs_interview, AUCUN headless, jamais faux-done.
    r1 = _Runner()
    rep1 = orchestrator.run_project(conn, settings, project=PROJECT, git=InternalGit(), runner=r1)
    assert "socle-design" in rep1.get("needs_interview", [])
    assert r1.calls == []                                            # zéro headless sur le socle
    assert _socle(conn)["interview"][0] == "todo"

    # 2 — cockpit interview : lance claude INTERACTIF ; l'humain remplit le design + author une feature.
    launched: list[list[str]] = []

    def human_launcher(argv, *, cwd, env=None):
        launched.append(list(argv))
        design = Path(cwd) / "docs" / "design.md"
        design.parent.mkdir(parents=True, exist_ok=True)
        design.write_text("# Design\n## Concept\nRoguelike de cartes ; jouable = 1 run.\n"
                          "## Boucle\nPiocher, arbitrer, risquer.\n## Économie\nPV 30, coût 2, gain 3.\n")
        model.add_feature(conn, project_slug=PROJECT, slug="backend-scaffold", facet="code")
        model.add_task(conn, feature_ref=f"{PROJECT}/backend-scaffold", slug="impl",
                       acceptance="Serveur répond /health 200, testé.")
        return 0

    rep2 = interview.run_interview(conn, settings, project=PROJECT, git=InternalGit(),
                                   launcher=human_launcher)
    assert rep2["ran"] is True and rep2["completed"] is True
    assert launched and launched[0][0] == "claude" and "-p" not in launched[0]   # interactif, pas headless
    assert "backend-scaffold" in _features(conn)                    # ≥1 feature de travail authorée
    assert all(status == "done" for status, _ in _socle(conn).values())          # socle clôturé (verified)

    # 3 — cockpit run AVANT le merge du socle : la feature de travail est TENUE (gate socle), pas de drain.
    r3 = _Runner()
    rep3 = orchestrator.run_project(conn, settings, project=PROJECT, git=InternalGit(), runner=r3)
    assert not rep3.get("needs_interview")                          # socle réconcilié, plus d'interview
    assert rep3.get("held_for_socle") == ["backend-scaffold"]       # tenue : socle pas encore mergé
    assert r3.calls == []                                           # aucun drain aval (dev sans design)

    # 4 — GO humain : merge le socle docs-only → le design atterrit sur dev (fail-closed levé délibérément).
    from cockpit.gate import merge
    done = merge.run_merge(conn, settings, feature_ref=f"{PROJECT}/socle-design", human_go=True,
                           git=InternalGit())
    assert done["merged"] is True                                   # design (docs-only) sur dev

    # 5 — cockpit run de nouveau : socle mergé → la feature de travail draine en HEADLESS depuis dev+design.
    r5 = _Runner()
    rep5 = orchestrator.run_project(conn, settings, project=PROJECT, git=InternalGit(), runner=r5)
    assert not rep5.get("needs_interview") and not rep5.get("held_for_socle")
    assert "backend-scaffold" in r5.calls                          # drain headless repris, socle sur dev
