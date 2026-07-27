"""Tests du data layer : projects/registry + roadmap/model sur une DB + projects_root jetables."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.projects import registry
from cockpit.roadmap import model

_GIT_ENV = {"PATH": os.environ.get("PATH", ""),
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}


def _make_upstream(path: Path, *, default: str = "main", extra: tuple[str, ...] = ()) -> Path:
    """Crée un vrai repo git local (upstream d'adoption) avec du contenu réel + des branches, utilisable
    comme `source_url` d'un `git clone --bare`. `default` = branche initiale ; `extra` = branches en plus."""
    path.mkdir(parents=True)
    run.run(["git", "init", "-q", "-b", default, str(path)], env=_GIT_ENV)
    (path / "README.md").write_text("# vrai contenu\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("print('real')\n", encoding="utf-8")
    run.run(["git", "-C", str(path), "add", "-A"], env=_GIT_ENV)
    run.run(["git", "-C", str(path), "commit", "-q", "-m", "real work"], env=_GIT_ENV)
    for b in extra:
        run.run(["git", "-C", str(path), "branch", b], env=_GIT_ENV)
    return path


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


def test_create_project_inits_bare_sot_and_persists(ctx):
    settings, conn = ctx
    p = registry.create_project(conn, settings, slug="demo-project", name="Demo")
    assert p["backend"] == "internal"
    # SoT bare réellement initialisé
    sot = registry.sot_path_for(settings, "demo-project")
    assert run.run(["git", "-C", str(sot), "rev-parse", "--is-bare-repository"]).stdout.strip() == "true"
    # persistance
    assert [x["slug"] for x in registry.list_projects(conn)] == ["demo-project"]
    assert registry.get_project(conn, "demo-project")["name"] == "Demo"


def test_create_project_seeds_selfworkable_toolkit(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="seeded")
    sot = registry.sot_path_for(settings, "seeded")
    # le SoT `dev` porte le toolkit auto-travaillable (semé à la création)
    names = run.run(["git", "-C", str(sot), "ls-tree", "-r", "--name-only", "dev"]).stdout.split()
    for expected in ("CLAUDE.md", ".gitignore", ".docsmap.toml", ".codemap.toml", ".frontmap.toml",
                     "docs/architecture.md", ".claude/settings.json",
                     ".claude/skills/work-loop/SKILL.md", ".claude/skills/quality-gate/SKILL.md"):
        assert expected in names, f"toolkit manque {expected} — {names}"
    # CLAUDE.md non vide et oriente vers l'outil (le levier « interroge, ne lis pas en bloc »)
    claude = run.run(["git", "-C", str(sot), "show", "dev:CLAUDE.md"]).stdout
    assert "docsmap where" in claude


def test_create_project_rejects_duplicate_and_bad_slug(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    with pytest.raises(ValueError):
        registry.create_project(conn, settings, slug="proj")   # doublon
    with pytest.raises(ValueError):
        registry.create_project(conn, settings, slug="Bad Slug")  # kebab invalide
    with pytest.raises(KeyError):
        registry.get_project(conn, "absent")


def test_create_entity_kind_and_owner_persist(ctx):
    settings, conn = ctx
    proj = registry.create_project(conn, settings, slug="a-project")
    tool = registry.create_project(conn, settings, slug="a-tool", kind="tool", owner="bosse")
    assert proj["kind"] == "project" and proj["owner"] is None            # défauts
    assert tool["kind"] == "tool" and tool["owner"] == "bosse"
    # persistance fidèle (relecture DB)
    assert registry.get_project(conn, "a-tool")["kind"] == "tool"
    assert registry.get_project(conn, "a-project")["kind"] == "project"
    # kind hors-enum rejeté AVANT tout effet (pas de SoT créé)
    with pytest.raises(ValueError):
        registry.create_project(conn, settings, slug="bad-kind", kind="widget")


# -- adoption : create_project(source_url=…) clone le VRAI contenu au lieu de semer -----------------

def test_create_project_adopts_real_repo_content(ctx, tmp_path):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "upstream", default="main", extra=("dev",))
    p = registry.create_project(conn, settings, slug="adopted", kind="tool", source_url=str(up))
    # provenance persistée (métadonnée, pas un secret)
    assert p["source_url"] == str(up) and p["kind"] == "tool"
    assert registry.get_project(conn, "adopted")["source_url"] == str(up)
    sot = registry.sot_path_for(settings, "adopted")
    # le SoT porte le VRAI historique cloné (≠ seed « root: cockpit seed »), dev+main présents
    names = run.run(["git", "-C", str(sot), "ls-tree", "-r", "--name-only", "dev"]).stdout.split()
    assert "src/app.py" in names and "README.md" in names
    assert "CLAUDE.md" not in names            # pas de toolkit semé — c'est un clone, pas un seed
    assert run.run(["git", "-C", str(sot), "show", "dev:README.md"]).stdout == "# vrai contenu\n"
    branches = run.run(["git", "-C", str(sot), "branch", "--format=%(refname:short)"]).stdout.split()
    assert {"dev", "main"} <= set(branches)


def test_adopt_normalizes_forge_branches_from_master_only(ctx, tmp_path):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "up-master", default="master")   # ni dev ni main en amont
    registry.create_project(conn, settings, slug="adopt-master", source_url=str(up))
    sot = registry.sot_path_for(settings, "adopt-master")
    branches = set(run.run(["git", "-C", str(sot), "branch", "--format=%(refname:short)"]).stdout.split())
    assert {"dev", "main"} <= branches            # synthétisées depuis master (invariant forge tenu)
    # dev/main pointent le même contenu réel que master
    assert run.run(["git", "-C", str(sot), "show", "dev:README.md"]).stdout == "# vrai contenu\n"


def test_adopt_bad_url_raises_and_leaves_no_row(ctx, tmp_path):
    settings, conn = ctx
    with pytest.raises(ValueError, match="clone échoué"):
        registry.create_project(conn, settings, slug="ghost", source_url=str(tmp_path / "n-existe-pas"))
    # clone échoué AVANT l'INSERT → aucune row orpheline (reprise de bootstrap propre)
    assert [x["slug"] for x in registry.list_projects(conn)] == []
    assert not registry.sot_path_for(settings, "ghost").exists()


def test_adopt_duplicate_slug_rejected(ctx, tmp_path):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u", default="main", extra=("dev",))
    registry.create_project(conn, settings, slug="dup", source_url=str(up))
    with pytest.raises(ValueError, match="déjà existant"):
        registry.create_project(conn, settings, slug="dup", source_url=str(up))


def test_ensure_columns_migrates_projects_v2_to_v3_in_place(tmp_path: Path):
    """Une base pré-v3 (projects sans kind/owner) migre en place : `ensure_columns` ajoute les colonnes,
    les lignes existantes prennent le défaut littéral `kind='project'` (ALTER), owner NULL."""
    import sqlite3

    from cockpit.db import schema
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    # table `projects` façon v2 (aucune colonne kind/owner)
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT "
                 "NULL, sot_path TEXT NOT NULL, mirror_remote TEXT, backend TEXT NOT NULL DEFAULT "
                 "'internal', created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO projects (id, slug, name, sot_path, backend, created_at) "
                 "VALUES ('i1', 'legacy', 'Legacy', '/x', 'internal', '2026-01-01')")
    conn.commit()
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    assert "kind" not in cols_before and "owner" not in cols_before
    schema.ensure_columns(conn)
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    assert {"kind", "owner"} <= cols_after
    row = conn.execute("SELECT kind, owner FROM projects WHERE slug = 'legacy'").fetchone()
    assert row["kind"] == "project" and row["owner"] is None   # défaut littéral appliqué à l'existant
    conn.close()


def test_ensure_columns_migrates_features_v9_to_v10_in_place(tmp_path: Path):
    """Une base pré-v10 (features sans depends_on) migre en place : `ensure_columns` ajoute la colonne,
    les lignes existantes prennent le défaut littéral `'[]'` (ALTER exige un défaut littéral, NOT NULL)."""
    import sqlite3

    from cockpit.db import schema
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    # table `features` façon v9 (facet/blueprint présents, PAS de depends_on)
    conn.execute("CREATE TABLE features (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, slug TEXT NOT NULL, "
                 "title TEXT NOT NULL, branch TEXT NOT NULL, worktree_path TEXT, status TEXT NOT NULL "
                 "DEFAULT 'planned', facet TEXT, blueprint TEXT, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO features (id, project_id, slug, title, branch, status, created_at) "
                 "VALUES ('f1', 'p1', 'legacy', 'Legacy', 'feature/legacy', 'planned', '2026-01-01')")
    conn.commit()
    assert "depends_on" not in {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    schema.ensure_columns(conn)
    assert "depends_on" in {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    row = conn.execute("SELECT depends_on FROM features WHERE slug = 'legacy'").fetchone()
    assert row["depends_on"] == "[]"                             # défaut littéral appliqué à l'existant
    conn.close()


def test_credential_ref_defaults_none_and_persists_at_create(ctx):
    settings, conn = ctx
    plain = registry.create_project(conn, settings, slug="plain")
    linked = registry.create_project(conn, settings, slug="linked", credential_ref="ref-42")
    assert plain["credential_ref"] is None                       # défaut : aucun token lié
    assert linked["credential_ref"] == "ref-42"
    assert registry.get_project(conn, "linked")["credential_ref"] == "ref-42"   # relecture DB fidèle


def test_set_credential_ref_links_and_unlinks(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    p = registry.set_credential_ref(conn, "proj", "ref-99")      # l'onboarding LIE la réf ici
    assert p["credential_ref"] == "ref-99"
    assert registry.get_project(conn, "proj")["credential_ref"] == "ref-99"
    unlinked = registry.set_credential_ref(conn, "proj", None)   # délier
    assert unlinked["credential_ref"] is None
    with pytest.raises(KeyError):
        registry.set_credential_ref(conn, "absent", "x")         # projet inexistant


def test_set_mirror_remote_configures_and_clears(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    assert registry.get_project(conn, "proj")["mirror_remote"] is None      # créé local-only
    p = registry.set_mirror_remote(conn, "proj", "https://github.com/moi/repo.git")
    assert p["mirror_remote"] == "https://github.com/moi/repo.git"          # rendu GitHub-backed
    assert registry.set_mirror_remote(conn, "proj", "  ")["mirror_remote"] is None   # vide → retiré
    assert registry.set_mirror_remote(conn, "proj", None)["mirror_remote"] is None   # null → retiré
    with pytest.raises(KeyError):
        registry.set_mirror_remote(conn, "absent", "x")                     # projet inexistant → 404


def test_ensure_columns_migrates_projects_v3_to_v4_in_place(tmp_path: Path):
    """Une base v3 (projects avec kind/owner mais sans credential_ref) migre en place : `ensure_columns`
    ajoute `credential_ref`, NULL pour l'existant (aucun défaut → pas de token lié rétroactivement)."""
    import sqlite3

    from cockpit.db import schema
    conn = sqlite3.connect(tmp_path / "v3.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT "
                 "NULL, sot_path TEXT NOT NULL, mirror_remote TEXT, backend TEXT NOT NULL DEFAULT "
                 "'internal', kind TEXT NOT NULL DEFAULT 'project', owner TEXT, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO projects (id, slug, name, sot_path, backend, kind, created_at) "
                 "VALUES ('i1', 'legacy', 'Legacy', '/x', 'internal', 'project', '2026-01-01')")
    conn.commit()
    assert "credential_ref" not in {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    schema.ensure_columns(conn)
    assert "credential_ref" in {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    row = conn.execute("SELECT credential_ref FROM projects WHERE slug = 'legacy'").fetchone()
    assert row["credential_ref"] is None                         # NULL rétroactif (pas de token)
    conn.close()


def test_add_feature_and_task_with_depends_on(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    f = model.add_feature(conn, project_slug="proj", slug="login", title="Login")
    assert f["branch"] == "feature/login"
    model.add_task(conn, feature_ref="proj/login", slug="schema", title="Schéma")
    model.add_task(conn, feature_ref="proj/login", slug="api", depends_on=["schema"], priority="P0")
    feat = model.resolve_feature(conn, "proj/login")
    tasks = {t["slug"]: t for t in model.list_tasks(conn, feat["id"])}
    assert tasks["api"]["depends_on"] == ["schema"]
    assert tasks["api"]["priority"] == "P0"


def test_add_task_validates_refs_and_priority(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    with pytest.raises(ValueError):
        model.add_task(conn, feature_ref="proj/feat", slug="t", priority="P9")   # priorité hors vocab
    with pytest.raises(ValueError):
        model.add_task(conn, feature_ref="nofeatureref", slug="t")               # ref sans '/'
    with pytest.raises(KeyError):
        model.add_task(conn, feature_ref="proj/absent", slug="t")                # feature absente


def test_set_feature_deps_edits_and_validates(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    for s in ("a", "b", "c"):
        model.add_feature(conn, project_slug="proj", slug=s)
    # happy : b dépend de a
    r = model.set_feature_deps(conn, ref="proj/b", depends_on=["a"])
    assert r["depends_on"] == ["a"]
    assert model.resolve_feature(conn, "proj/b")["depends_on"] == ["a"]   # persisté (commit)
    # dangling → refus + rollback (b garde ['a'])
    with pytest.raises(ValueError):
        model.set_feature_deps(conn, ref="proj/b", depends_on=["ghost"])
    assert model.resolve_feature(conn, "proj/b")["depends_on"] == ["a"]
    # self-dep → refus (cycle)
    with pytest.raises(ValueError):
        model.set_feature_deps(conn, ref="proj/b", depends_on=["b"])
    # cycle a→b (b→a existe déjà) → refus + rollback (a reste vide)
    with pytest.raises(ValueError):
        model.set_feature_deps(conn, ref="proj/a", depends_on=["b"])
    assert model.resolve_feature(conn, "proj/a")["depends_on"] == []
    # feature cible inconnue → KeyError
    with pytest.raises(KeyError):
        model.set_feature_deps(conn, ref="proj/absent", depends_on=[])
    # remplace-sémantique : liste vide efface
    model.set_feature_deps(conn, ref="proj/b", depends_on=[])
    assert model.resolve_feature(conn, "proj/b")["depends_on"] == []


def test_set_feature_deps_scoped_by_project(ctx):
    settings, conn = ctx
    for p in ("p1", "p2"):
        registry.create_project(conn, settings, slug=p)
        model.add_feature(conn, project_slug=p, slug="a")
        model.add_feature(conn, project_slug=p, slug="b")     # même slug dans les 2 projets
    model.set_feature_deps(conn, ref="p1/b", depends_on=["a"])
    assert model.resolve_feature(conn, "p1/b")["depends_on"] == ["a"]
    assert model.resolve_feature(conn, "p2/b")["depends_on"] == []    # l'autre projet intact (scope par id)


def test_set_task_deps_edits_and_validates(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    for s in ("t1", "t2", "t3"):
        model.add_task(conn, feature_ref="proj/feat", slug=s)
    feat_id = model.resolve_feature(conn, "proj/feat")["id"]
    # happy : t2 dépend de t1
    r = model.set_task_deps(conn, feature_ref="proj/feat", slug="t2", depends_on=["t1"])
    assert r["depends_on"] == ["t1"]
    assert {t["slug"]: t for t in model.list_tasks(conn, feat_id)}["t2"]["depends_on"] == ["t1"]
    # dangling → refus + rollback
    with pytest.raises(ValueError):
        model.set_task_deps(conn, feature_ref="proj/feat", slug="t2", depends_on=["ghost"])
    assert {t["slug"]: t for t in model.list_tasks(conn, feat_id)}["t2"]["depends_on"] == ["t1"]
    # self-dep + cycle → refus
    with pytest.raises(ValueError):
        model.set_task_deps(conn, feature_ref="proj/feat", slug="t3", depends_on=["t3"])
    with pytest.raises(ValueError):
        model.set_task_deps(conn, feature_ref="proj/feat", slug="t1", depends_on=["t2"])
    # task cible inconnue → KeyError ; feature inconnue → KeyError
    with pytest.raises(KeyError):
        model.set_task_deps(conn, feature_ref="proj/feat", slug="absent", depends_on=[])
    with pytest.raises(KeyError):
        model.set_task_deps(conn, feature_ref="proj/absent", slug="t1", depends_on=[])


def test_roadmap_to_yaml_contract(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat", title="Feat")
    model.add_task(conn, feature_ref="proj/feat", slug="a")
    model.add_task(conn, feature_ref="proj/feat", slug="b", depends_on=["a"])
    features = model.list_features(conn, "proj")
    for f in features:
        f["tasks"] = model.list_tasks(conn, f["id"])
    doc = yaml.safe_load(model.to_yaml("proj", features))
    assert doc["version"] == model.ROADMAP_VERSION
    assert doc["project"] == "proj"
    assert doc["features"][0]["slug"] == "feat"
    b = next(t for t in doc["features"][0]["tasks"] if t["slug"] == "b")
    assert b["depends_on"] == ["a"]


# -- v6 (typed-bundles) : project_type / features.facet / tasks.acceptance -------------------------

def test_create_project_defaults_generic_type_and_persists(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    # défaut DDL appliqué : un projet non typé est `generic` (relecture DB fidèle)
    assert registry.get_project(conn, "proj")["project_type"] == "generic"


def test_create_typed_project_seeds_overlay_and_persists_type(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="svc", project_type="service-api")
    assert registry.get_project(conn, "svc")["project_type"] == "service-api"   # type persisté
    sot = registry.sot_path_for(settings, "svc")
    names = run.run(["git", "-C", str(sot), "ls-tree", "-r", "--name-only", "dev"]).stdout.split()
    # le SoT porte la couche base ET l'overlay du type (facette backend + bundle.toml typé)
    assert ".claude/facets/backend/PERSONA.md" in names
    assert ".cockpit/bundle.toml" in names
    assert "CLAUDE.md" in names                                                  # base conservée
    # subtilité .gitignore RÉELLE : `.claude/*.local.json` ne traverse pas `/` → la SOURCE nichée de facette
    # est committée (seule la copie activée `.claude/settings.local.json` sera ignorée au dispatch).
    assert ".claude/facets/backend/settings.local.json" in names
    arch = run.run(["git", "-C", str(sot), "show", "dev:docs/architecture.md"]).stdout
    assert "service / API" in arch                                              # doc pré-optimisée du type


def test_create_project_stamps_provenance_in_sot(ctx):
    """P3 : tout SoT semé porte un tampon `.cockpit/provenance.toml` = de quel `bundle@version` il DÉRIVE,
    et quand (SoT-and-derive → dérive détectable, re-sync opt-in). Un typé stampe son type ; le générique
    stampe `generic`. `created_at` cohérent avec la row DB (même horodatage)."""
    settings, conn = ctx
    import tomllib
    p = registry.create_project(conn, settings, slug="void-runner", project_type="browser-game")
    sot = registry.sot_path_for(settings, "void-runner")
    names = run.run(["git", "-C", str(sot), "ls-tree", "-r", "--name-only", "dev"]).stdout.split()
    assert ".cockpit/provenance.toml" in names
    stamp = tomllib.loads(
        run.run(["git", "-C", str(sot), "show", "dev:.cockpit/provenance.toml"]).stdout)["provenance"]
    assert stamp["bundle"] == "browser-game"
    assert stamp["version"] == "1"
    assert stamp["created_at"] == p["created_at"]                 # tampon SoT ≡ row DB (même horodatage)
    # le générique stampe aussi (bundle = generic) — la provenance est universelle, pas réservée aux typés
    registry.create_project(conn, settings, slug="plain")
    gen = run.run(["git", "-C", str(registry.sot_path_for(settings, "plain")),
                   "show", "dev:.cockpit/provenance.toml"]).stdout
    assert tomllib.loads(gen)["provenance"]["bundle"] == "generic"


def test_create_project_does_not_re_derive_at_seed(ctx, monkeypatch):
    """VERROU : `create_project` lit les octets DÉJÀ dérivés (verbatim/offline) et ne re-dérive JAMAIS au
    seed. On neutralise le générateur (derive_type/apply_derivation → boom) : la création d'un projet du type
    dérivé doit quand même réussir (elle ne lit que le manifeste, pas le moteur)."""
    settings, conn = ctx
    from cockpit.provision import derive

    def _boom(*a, **k):
        raise AssertionError("create_project ne doit PAS re-dériver au seed")
    monkeypatch.setattr(derive, "derive_type", _boom)
    monkeypatch.setattr(derive, "apply_derivation", _boom)
    p = registry.create_project(conn, settings, slug="void-runner", project_type="browser-game")
    assert p["slug"] == "void-runner"                              # seed réussi sans toucher le générateur


def test_create_project_rejects_unknown_type_before_any_effect(ctx):
    settings, conn = ctx
    with pytest.raises(ValueError, match="inconnu"):
        registry.create_project(conn, settings, slug="bad", project_type="rust")
    assert [x["slug"] for x in registry.list_projects(conn)] == []              # aucun effet
    assert not registry.sot_path_for(settings, "bad").exists()


def test_add_feature_facet_and_task_acceptance_round_trip(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")   # bundle déclare backend
    f = model.add_feature(conn, project_slug="proj", slug="api", title="API", facet="backend")
    assert f["facet"] == "backend"
    model.add_task(conn, feature_ref="proj/api", slug="schema",
                   acceptance="Le endpoint /health répond 200 et un test le couvre.")
    feat = model.resolve_feature(conn, "proj/api")
    assert feat["facet"] == "backend"                                    # relecture DB
    task = next(t for t in model.list_tasks(conn, feat["id"]) if t["slug"] == "schema")
    assert "endpoint /health" in task["acceptance"]                      # critères persistés


def test_add_feature_rejects_bad_facet(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")                          # generic → facets = {doc}
    with pytest.raises(ValueError):
        model.add_feature(conn, project_slug="proj", slug="x", facet="widget")    # hors vocab du bundle


def test_add_feature_facet_is_registry_driven_per_project(ctx):
    """La facette valide d'une feature = les facettes du bundle DU projet (registre), pas un enum global.
    Débloque `game-design` sur browser-game (le DoD) ET durcit le cas latent (backend sur un generic)."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="game", project_type="browser-game")
    registry.create_project(conn, settings, slug="svc", project_type="service-api")
    registry.create_project(conn, settings, slug="plain")                         # generic → {doc}
    # game-design est ACCEPTÉE sur browser-game (son bundle la déclare) — c'était impossible avant P4.
    gd = model.add_feature(conn, project_slug="game", slug="hud", facet="game-design")
    assert gd["facet"] == "game-design"
    # la MÊME facette est REJETÉE sur un service-api (son bundle ne la déclare pas)…
    with pytest.raises(ValueError, match="hors vocab"):
        model.add_feature(conn, project_slug="svc", slug="hud", facet="game-design")
    # …et `backend` sur un generic est désormais fail-closed (avant P4 : accepté à tort → persona-fantôme).
    with pytest.raises(ValueError, match="hors vocab"):
        model.add_feature(conn, project_slug="plain", slug="api", facet="backend")


def test_add_feature_facet_none_is_default(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    f = model.add_feature(conn, project_slug="proj", slug="misc")        # facet omis → NULL (défaut bundle)
    assert f["facet"] is None
    assert model.resolve_feature(conn, "proj/misc")["facet"] is None


def test_roadmap_to_yaml_carries_facet_and_acceptance(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")   # bundle déclare frontend
    model.add_feature(conn, project_slug="proj", slug="ui", title="UI", facet="frontend")
    model.add_feature(conn, project_slug="proj", slug="plain")           # ni facette ni acceptance
    model.add_task(conn, feature_ref="proj/ui", slug="screen", acceptance="Le login s'affiche.")
    model.add_task(conn, feature_ref="proj/plain", slug="misc")
    features = model.list_features(conn, "proj")
    for f in features:
        f["tasks"] = model.list_tasks(conn, f["id"])
    doc = yaml.safe_load(model.to_yaml("proj", features))
    ui = next(f for f in doc["features"] if f["slug"] == "ui")
    plain = next(f for f in doc["features"] if f["slug"] == "plain")
    assert ui["facet"] == "frontend"                                     # facette émise si présente
    assert ui["tasks"][0]["acceptance"] == "Le login s'affiche."         # critères émis si présents
    assert "facet" not in plain                                          # rétro-compat : absents si non posés
    assert "acceptance" not in plain["tasks"][0]


def test_task_mode_round_trip_and_validation(ctx):
    """v12 : `mode` (headless|interactive) est porté par la row et émis au yaml SEULEMENT si interactif
    (rétro-compat : une task headless reste identique au contrat v1). Un mode hors vocab lève ValueError."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="cadrage-feat", facet="doc")
    t = model.add_task(conn, feature_ref="proj/cadrage-feat", slug="cadrage",
                       acceptance="Intention renseignée.", mode="interactive")
    assert t["mode"] == "interactive"                                    # row rendue porte le mode
    model.add_task(conn, feature_ref="proj/cadrage-feat", slug="build", acceptance="Code posé.")  # headless
    with pytest.raises(ValueError):
        model.add_task(conn, feature_ref="proj/cadrage-feat", slug="bad", mode="daemon")  # mode hors vocab
    features = model.list_features(conn, "proj")
    for f in features:
        f["tasks"] = model.list_tasks(conn, f["id"])
    doc = yaml.safe_load(model.to_yaml("proj", features))
    feat = next(f for f in doc["features"] if f["slug"] == "cadrage-feat")
    tasks = {t["slug"]: t for t in feat["tasks"]}
    assert tasks["cadrage"]["mode"] == "interactive"                     # interactif → émis
    assert "mode" not in tasks["build"]                                  # headless (défaut) → omis


def test_add_feature_inter_feature_depends_on_round_trip(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="design", facet="backend")
    f = model.add_feature(conn, project_slug="proj", slug="code", facet="backend", depends_on=["design"])
    assert f["depends_on"] == ["design"]                                 # row RENDU décodé (pas la string)
    assert model.resolve_feature(conn, "proj/code")["depends_on"] == ["design"]   # relecture DB décodée
    plain = model.add_feature(conn, project_slug="proj", slug="misc")    # aucune dep inter-feature
    assert plain["depends_on"] == []


def test_roadmap_to_yaml_carries_feature_depends_on(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj", project_type="front-ts")
    model.add_feature(conn, project_slug="proj", slug="design", facet="backend")
    model.add_feature(conn, project_slug="proj", slug="code", facet="backend", depends_on=["design"])
    features = model.list_features(conn, "proj")
    doc = yaml.safe_load(model.to_yaml("proj", features))
    code = next(f for f in doc["features"] if f["slug"] == "code")
    design = next(f for f in doc["features"] if f["slug"] == "design")
    assert code["depends_on"] == ["design"]                              # DAG inter-feature émis si présent
    assert "depends_on" not in design                                    # rétro-compat : absent si vide


def test_add_feature_blueprint_round_trip_and_yaml(ctx):
    """`blueprint` (v9) = ref STAMP portée par une feature : stockée telle quelle (id brut), relue en DB, et
    émise dans `roadmap.yaml` SEULEMENT si présente (rétro-compat, comme `facet`). Aucune résolution ici —
    la ref reste un id opaque au niveau modèle ; le board la résout via MCP."""
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    f = model.add_feature(conn, project_slug="proj", slug="gate", title="Gate",
                          blueprint="deterministic-tooling-gate")
    assert f["blueprint"] == "deterministic-tooling-gate"
    reread = model.resolve_feature(conn, "proj/gate")                    # relecture DB
    assert reread["blueprint"] == "deterministic-tooling-gate"
    model.add_feature(conn, project_slug="proj", slug="plain")           # sans blueprint → NULL
    assert model.resolve_feature(conn, "proj/plain")["blueprint"] is None
    features = model.list_features(conn, "proj")
    for f in features:
        f["tasks"] = model.list_tasks(conn, f["id"])
    doc = yaml.safe_load(model.to_yaml("proj", features))
    gate = next(f for f in doc["features"] if f["slug"] == "gate")
    plain = next(f for f in doc["features"] if f["slug"] == "plain")
    assert gate["blueprint"] == "deterministic-tooling-gate"             # ref brute émise si présente
    assert "blueprint" not in plain                                      # rétro-compat : absent si non posé


def test_ensure_columns_migrates_v5_to_v6_in_place(tmp_path: Path):
    """Une base v5 (projects sans project_type ; features sans facet ; tasks sans acceptance) migre en
    place : `ensure_columns` ajoute les 3 colonnes ; l'existant prend `project_type='generic'` (défaut
    littéral ALTER), facet/acceptance NULL (nullables, aucun défaut)."""
    import sqlite3

    from cockpit.db import schema
    conn = sqlite3.connect(tmp_path / "v5.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT "
                 "NULL, sot_path TEXT NOT NULL, mirror_remote TEXT, backend TEXT NOT NULL DEFAULT "
                 "'internal', kind TEXT NOT NULL DEFAULT 'project', owner TEXT, credential_ref TEXT, "
                 "source_url TEXT, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE features (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, slug TEXT NOT "
                 "NULL, title TEXT NOT NULL, branch TEXT NOT NULL, worktree_path TEXT, status TEXT NOT "
                 "NULL DEFAULT 'planned', created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, feature_id TEXT NOT NULL, slug TEXT NOT NULL, "
                 "title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'todo', depends_on TEXT NOT NULL "
                 "DEFAULT '[]', priority TEXT NOT NULL DEFAULT 'P1', created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO projects (id, slug, name, sot_path, backend, kind, created_at) "
                 "VALUES ('i1', 'legacy', 'Legacy', '/x', 'internal', 'project', '2026-01-01')")
    conn.execute("INSERT INTO features (id, project_id, slug, title, branch, status, created_at) "
                 "VALUES ('f1', 'i1', 'feat', 'Feat', 'feature/feat', 'planned', '2026-01-01')")
    conn.execute("INSERT INTO tasks (id, feature_id, slug, title, created_at) "
                 "VALUES ('t1', 'f1', 'task', 'Task', '2026-01-01')")
    conn.commit()
    assert "project_type" not in {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    schema.ensure_columns(conn)
    assert "project_type" in {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    assert "facet" in {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    assert "acceptance" in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert conn.execute("SELECT project_type FROM projects WHERE slug='legacy'").fetchone()[0] == "generic"
    assert conn.execute("SELECT facet FROM features WHERE slug='feat'").fetchone()[0] is None
    assert conn.execute("SELECT acceptance FROM tasks WHERE slug='task'").fetchone()[0] is None


def test_migrate_v8_drops_project_type_check(tmp_path: Path):
    """Une base v7 portant le `CHECK` figé sur `project_type` migre en v8 par rebuild de table : le CHECK
    disparaît (enum registre-driven), les données sont préservées, et un `project_type` hors ancien enum est
    désormais accepté côté DB. Gardé (no-op sans CHECK) + idempotent au niveau du gate de migration."""
    import sqlite3

    from cockpit.db import schema, store
    conn = store.connect(tmp_path / "v7.db")
    conn.executescript(
        "CREATE TABLE projects ("
        " id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, sot_path TEXT NOT NULL,"
        " mirror_remote TEXT, backend TEXT NOT NULL DEFAULT 'internal',"
        " kind TEXT NOT NULL DEFAULT 'project', owner TEXT,"
        " credential_ref TEXT, source_url TEXT,"
        " project_type TEXT NOT NULL DEFAULT 'generic'"
        "   CHECK (project_type IN ('generic','service-api','cli-tool','front-ts')),"
        " created_at TEXT NOT NULL);")
    conn.execute("INSERT INTO projects (id, slug, name, sot_path, backend, kind, project_type, created_at) "
                 "VALUES ('i1','keep','Keep','/x','internal','project','service-api','2026-01-01')")
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    # avant migration : le CHECK figé rejette un type hors ancien enum
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO projects (id, slug, name, sot_path, created_at, project_type) "
                     "VALUES ('i2','bad','Bad','/y','2026-01-01','browser-game')")
    conn.rollback()

    assert store.migrate(conn) == schema.SCHEMA_VERSION                     # migre → v8 (rebuild de table)
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()[0]
    assert "CHECK (project_type IN" not in ddl                             # le CHECK a disparu
    assert conn.execute("SELECT project_type FROM projects WHERE slug='keep'").fetchone()[0] == "service-api"
    # après migration : un type hors ancien enum est accepté côté DB (autorité désormais applicative)
    conn.execute("INSERT INTO projects (id, slug, name, sot_path, backend, kind, project_type, created_at) "
                 "VALUES ('i3','bg','BG','/z','internal','project','browser-game','2026-01-01')")
    conn.commit()
    assert conn.execute("SELECT project_type FROM projects WHERE slug='bg'").fetchone()[0] == "browser-game"
    assert store.migrate(conn) == schema.SCHEMA_VERSION                     # idempotent (gate de migration)
    conn.close()
    conn.close()


def test_migrate_v15_adds_rate_limited_to_dispatch_status_check(tmp_path: Path):
    """Une base v14 dont `dispatch_jobs.status` porte l'ancien CHECK (sans `rate_limited`) migre en v15 par
    rebuild de table : le nouveau statut est accepté, les données sont préservées, et l'ancien enum reste
    valable. SQLite ne sait pas ALTER un CHECK → rebuild (patron v8). Gardé + idempotent."""
    import sqlite3

    from cockpit.db import schema, store
    conn = store.connect(tmp_path / "v14.db")
    conn.execute("PRAGMA foreign_keys = OFF")   # ce test cible le CHECK de `status`, pas la FK task_id
    conn.executescript(
        "CREATE TABLE dispatch_jobs ("
        " id TEXT PRIMARY KEY,"
        " task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,"
        " worktree_path TEXT NOT NULL, port INTEGER, pid INTEGER,"
        " status TEXT NOT NULL DEFAULT 'pending'"
        "   CHECK (status IN ('pending','running','done','failed','killed')),"
        " kind TEXT NOT NULL DEFAULT 'task' CHECK (kind IN ('task','review','toolchain','fix')),"
        " log_path TEXT, session_id TEXT, num_turns INTEGER, cost_usd REAL, wall_s REAL, engine TEXT,"
        " input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,"
        " cache_creation_tokens INTEGER, model TEXT, error TEXT, started_at TEXT, ended_at TEXT);")
    conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, status) "
                 "VALUES ('j1','tk1','/wt','done')")
    conn.execute("PRAGMA user_version = 14")
    conn.commit()
    # avant migration : l'ancien CHECK rejette `rate_limited`
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, status) "
                     "VALUES ('j2','tk1','/wt','rate_limited')")
    conn.rollback()

    assert store.migrate(conn) == schema.SCHEMA_VERSION                     # migre → v15 (rebuild de table)
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='dispatch_jobs'").fetchone()[0]
    assert "rate_limited" in ddl                                           # le CHECK a gagné le statut
    assert conn.execute("SELECT status FROM dispatch_jobs WHERE id='j1'").fetchone()[0] == "done"  # préservé
    # après migration : `rate_limited` est accepté côté DB
    conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, status) "
                 "VALUES ('j3','tk1','/wt','rate_limited')")
    conn.commit()
    assert conn.execute("SELECT status FROM dispatch_jobs WHERE id='j3'").fetchone()[0] == "rate_limited"
    assert store.migrate(conn) == schema.SCHEMA_VERSION                     # idempotent
    conn.close()


def test_migrate_v16_adds_interrupted_to_dispatch_status_check(tmp_path: Path):
    """Une base v15 dont `dispatch_jobs.status` porte le CHECK v15 (avec `rate_limited`, sans `interrupted`)
    migre en v16 par rebuild de table : le statut `interrupted` est accepté, les données préservées, et
    `rate_limited` (v15) reste valable. SQLite n'ALTER pas un CHECK → rebuild (patron v8). Idempotent."""
    import sqlite3

    from cockpit.db import schema, store
    conn = store.connect(tmp_path / "v15.db")
    conn.execute("PRAGMA foreign_keys = OFF")   # ce test cible le CHECK de `status`, pas la FK task_id
    conn.executescript(
        "CREATE TABLE dispatch_jobs ("
        " id TEXT PRIMARY KEY,"
        " task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,"
        " worktree_path TEXT NOT NULL, port INTEGER, pid INTEGER,"
        " status TEXT NOT NULL DEFAULT 'pending'"
        "   CHECK (status IN ('pending','running','done','failed','killed','rate_limited')),"
        " kind TEXT NOT NULL DEFAULT 'task' CHECK (kind IN ('task','review','toolchain','fix')),"
        " log_path TEXT, session_id TEXT, num_turns INTEGER, cost_usd REAL, wall_s REAL, engine TEXT,"
        " input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,"
        " cache_creation_tokens INTEGER, model TEXT, error TEXT, started_at TEXT, ended_at TEXT);")
    conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, status) "
                 "VALUES ('j1','tk1','/wt','rate_limited')")   # un statut v15 → doit être préservé
    conn.execute("PRAGMA user_version = 15")
    conn.commit()
    # avant migration : le CHECK v15 rejette `interrupted`
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, status) "
                     "VALUES ('j2','tk1','/wt','interrupted')")
    conn.rollback()

    assert store.migrate(conn) == schema.SCHEMA_VERSION                     # migre → v16 (rebuild de table)
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='dispatch_jobs'").fetchone()[0]
    assert "interrupted" in ddl and "rate_limited" in ddl                  # le CHECK porte les DEUX ajouts
    assert conn.execute(                                                   # donnée v15 préservée
        "SELECT status FROM dispatch_jobs WHERE id='j1'").fetchone()[0] == "rate_limited"
    # après migration : `interrupted` est accepté côté DB
    conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, status) "
                 "VALUES ('j3','tk1','/wt','interrupted')")
    conn.commit()
    assert conn.execute("SELECT status FROM dispatch_jobs WHERE id='j3'").fetchone()[0] == "interrupted"
    assert store.migrate(conn) == schema.SCHEMA_VERSION                     # idempotent
    conn.close()


def test_ensure_columns_migrates_v8_to_v9_in_place(tmp_path: Path):
    """Une base v8 (features avec facet, sans blueprint) migre en place : `ensure_columns` ajoute la colonne
    `blueprint` (nullable, aucun défaut → NULL pour l'existant). Additif, idempotent (2ᵉ appel no-op)."""
    import sqlite3

    from cockpit.db import schema
    conn = sqlite3.connect(tmp_path / "v8.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE features (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, slug TEXT NOT "
                 "NULL, title TEXT NOT NULL, branch TEXT NOT NULL, worktree_path TEXT, status TEXT NOT "
                 "NULL DEFAULT 'planned', facet TEXT, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO features (id, project_id, slug, title, branch, status, facet, created_at) "
                 "VALUES ('f1', 'i1', 'feat', 'Feat', 'feature/feat', 'planned', 'backend', '2026-01-01')")
    conn.commit()
    assert "blueprint" not in {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    schema.ensure_columns(conn)
    assert "blueprint" in {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    assert conn.execute("SELECT blueprint FROM features WHERE slug='feat'").fetchone()[0] is None
    assert conn.execute("SELECT facet FROM features WHERE slug='feat'").fetchone()[0] == "backend"  # préservé
    schema.ensure_columns(conn)                                            # 2ᵉ appel : no-op (ALTER gardé)
    assert "blueprint" in {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    conn.close()


def test_ensure_columns_migrates_v10_to_v11_in_place(tmp_path: Path):
    """Une base v10 (dispatch_jobs sans kind/error) migre en place : `ensure_columns` ajoute `kind` (NOT NULL
    DEFAULT 'task' → les jobs existants, tous des runs d'ouvrier, prennent 'task') et `error` (nullable → NULL
    pour l'existant). Additif, idempotent (2ᵉ appel no-op)."""
    import sqlite3

    from cockpit.db import schema
    conn = sqlite3.connect(tmp_path / "v10.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE dispatch_jobs (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, worktree_path "
                 "TEXT NOT NULL, port INTEGER, pid INTEGER, status TEXT NOT NULL DEFAULT 'pending', "
                 "log_path TEXT, session_id TEXT, num_turns INTEGER, cost_usd REAL, wall_s REAL, engine "
                 "TEXT, started_at TEXT, ended_at TEXT)")
    conn.execute("INSERT INTO dispatch_jobs (id, task_id, worktree_path, status) "
                 "VALUES ('j1', 't1', '/wt', 'done')")
    conn.commit()
    assert "kind" not in {r[1] for r in conn.execute("PRAGMA table_info(dispatch_jobs)")}
    schema.ensure_columns(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dispatch_jobs)")}
    assert "kind" in cols and "error" in cols
    row = conn.execute("SELECT kind, error FROM dispatch_jobs WHERE id='j1'").fetchone()
    assert row["kind"] == "task" and row["error"] is None                  # défaut littéral + nullable
    schema.ensure_columns(conn)                                            # 2ᵉ appel : no-op (ALTER gardé)
    assert conn.execute("SELECT kind FROM dispatch_jobs WHERE id='j1'").fetchone()["kind"] == "task"
    conn.close()


def test_migration_v10_to_v11_creates_trace_tables_in_place(tmp_path: Path):
    """Une base v10 (sans `non_runs`/`gate_verdicts`) migre en place : `store.migrate` (via `create_schema` +
    `CREATE IF NOT EXISTS`) crée les tables neuves et pose la `SCHEMA_VERSION` courante (sans colonnes)."""
    from cockpit.db import schema
    conn = store.connect(tmp_path / "v10.db")
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT "
                 "NULL, sot_path TEXT NOT NULL, backend TEXT NOT NULL DEFAULT 'internal', "
                 "created_at TEXT NOT NULL)")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "non_runs" not in before and "gate_verdicts" not in before

    assert store.migrate(conn) == schema.SCHEMA_VERSION          # migre → version courante
    after = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"non_runs", "gate_verdicts"} <= after
    assert schema.schema_version(conn) == schema.SCHEMA_VERSION
    conn.close()


def test_ensure_columns_migrates_v11_to_v12_adds_task_mode_in_place(tmp_path: Path):
    """Une base v11 (tasks sans `mode`) migre en place : `ensure_columns` ajoute `mode` (NOT NULL DEFAULT
    'headless' → les tasks existantes, toutes des runs headless, prennent 'headless'). Additif, idempotent."""
    import sqlite3

    from cockpit.db import schema
    conn = sqlite3.connect(tmp_path / "v11.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, feature_id TEXT NOT NULL, slug TEXT NOT NULL, "
                 "title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'todo', depends_on TEXT NOT NULL "
                 "DEFAULT '[]', priority TEXT NOT NULL DEFAULT 'P1', acceptance TEXT, "
                 "created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO tasks (id, feature_id, slug, title, created_at) "
                 "VALUES ('t1', 'f1', 'cadrage', 'Cadrage', '2026-07-18')")
    conn.commit()
    assert "mode" not in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    schema.ensure_columns(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "mode" in cols
    assert conn.execute("SELECT mode FROM tasks WHERE id='t1'").fetchone()["mode"] == "headless"  # défaut
    schema.ensure_columns(conn)                                            # 2ᵉ appel : no-op (ALTER gardé)
    assert conn.execute("SELECT mode FROM tasks WHERE id='t1'").fetchone()["mode"] == "headless"
    conn.close()


def test_migrate_v16_to_v17_adds_alerts_table_in_place(tmp_path: Path):
    """v17 (no-silent-block) : la table `alerts` est BRAND-NEW → ajoutée en place sur une base v16 par
    `CREATE IF NOT EXISTS` (précédent v11 `non_runs`), SANS rebuild. Données préservées, index unique partiel
    `ux_alerts_open` posé, migration idempotente."""
    import sqlite3

    from cockpit.db import schema, store
    conn = store.connect(tmp_path / "v16.db")
    schema.create_schema(conn)                          # base à jour…
    conn.execute("DROP INDEX ux_alerts_open")
    conn.execute("DROP TABLE alerts")                   # …qu'on ramène à un état « pré-v17 » (sans alerts)
    conn.execute("PRAGMA user_version = 16")
    conn.commit()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "alerts" not in tables

    assert store.migrate(conn) == schema.SCHEMA_VERSION         # migre en place (alerts revient)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "alerts" in tables and "ux_alerts_open" in idx and "ix_alerts_status" in idx
    # l'index unique partiel tient : une 2e ligne OUVERTE de même (project, feature_ref, kind) est rejetée
    conn.execute("INSERT INTO alerts (id, project, feature_ref, feature, kind, reason, status, "
                 "created_at, updated_at) VALUES ('a1','p','p/f','f','gate_red','r','open','t','t')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO alerts (id, project, feature_ref, feature, kind, reason, status, "
                     "created_at, updated_at) VALUES ('a2','p','p/f','f','gate_red','r2','open','t','t')")
    conn.rollback()
    conn.execute("INSERT INTO alerts (id, project, feature_ref, feature, kind, reason, status, "
                 "created_at, updated_at) VALUES ('a1','p','p/f','f','gate_red','r','open','t','t')")
    conn.commit()
    assert store.migrate(conn) == schema.SCHEMA_VERSION   # idempotent
    assert conn.execute("SELECT reason FROM alerts WHERE id='a1'").fetchone()[0] == "r"   # donnée préservée
    conn.close()


def test_migrate_v17_to_v18_adds_merge_outcomes_table_in_place(tmp_path: Path):
    """v18 (gate-green-outcome) : la table `merge_outcomes` est BRAND-NEW → ajoutée en place sur une base v17
    par `CREATE IF NOT EXISTS` (précédent v17 `alerts`), SANS rebuild. Données préservées, index unique
    `ux_merge_outcome` posé, migration idempotente."""
    import sqlite3

    from cockpit.db import schema, store
    conn = store.connect(tmp_path / "v17.db")
    schema.create_schema(conn)                          # base à jour…
    conn.execute("DROP INDEX ux_merge_outcome")
    conn.execute("DROP TABLE merge_outcomes")           # …ramenée à un état « pré-v18 » (sans merge_outcomes)
    conn.execute("PRAGMA user_version = 17")
    conn.commit()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "merge_outcomes" not in tables

    assert store.migrate(conn) == schema.SCHEMA_VERSION         # migre en place (merge_outcomes revient)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "merge_outcomes" in tables and "ux_merge_outcome" in idx
    # l'index unique tient : un 2e merge de même (project, feature, sha) est rejeté
    conn.execute("INSERT INTO merge_outcomes (id, project, feature, feature_ref, sha, merged_at, updated_at) "
                 "VALUES ('m1','p','f','p/f','abc','t','t')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO merge_outcomes (id, project, feature, feature_ref, sha, merged_at, "
                     "updated_at) VALUES ('m2','p','f','p/f','abc','t','t')")
    conn.rollback()
    conn.execute("INSERT INTO merge_outcomes (id, project, feature, feature_ref, sha, merged_at, updated_at) "
                 "VALUES ('m1','p','f','p/f','abc','t','t')")
    conn.commit()
    assert store.migrate(conn) == schema.SCHEMA_VERSION   # idempotent
    assert conn.execute("SELECT outcome FROM merge_outcomes WHERE id='m1'").fetchone()[0] == "held"
    conn.close()


def test_migrate_v18_to_v19_adds_review_findings_to_alerts_kind_check(tmp_path: Path):
    """Une base v18 dont `alerts.kind` porte l'ancien CHECK (6 kinds, sans `review_findings`) migre en v19 par
    rebuild de table : le nouveau kind est accepté, les données préservées, l'ancien enum reste valable.
    CRITIQUE : le rebuild DROP la table → les index sont recréés DANS la migration (contrairement à v15/v16),
    car `ux_alerts_open` est la cible de l'`ON CONFLICT` d'`emit_alert` (sans lui l'UPSERT casserait)."""
    import sqlite3

    from cockpit.db import alerts, schema, store
    conn = store.connect(tmp_path / "v18.db")
    conn.executescript(
        "CREATE TABLE alerts ("
        " id TEXT PRIMARY KEY, project TEXT NOT NULL, feature_ref TEXT NOT NULL, feature TEXT NOT NULL,"
        " kind TEXT NOT NULL CHECK (kind IN ('gate_red','worker_failed','rate_limited','interrupted',"
        "   'socle_hold','interview_hold')),"
        " tier TEXT CHECK (tier IS NULL OR tier IN ('tier0','tier1','tier1.5','native')),"
        " severity TEXT NOT NULL DEFAULT 'blocker' CHECK (severity IN ('blocker','warn','info')),"
        " reason TEXT NOT NULL, findings TEXT,"
        " status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','acked','resolved')),"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT);"
        "CREATE UNIQUE INDEX ux_alerts_open ON alerts(project,feature_ref,kind) WHERE status='open';"
        "CREATE INDEX ix_alerts_status ON alerts(status);")
    conn.execute("INSERT INTO alerts (id,project,feature_ref,feature,kind,severity,reason,status,"
                 "created_at,updated_at) VALUES ('a1','p','p/f','f','gate_red','blocker','r','open','t','t')")
    conn.execute("PRAGMA user_version = 18")
    conn.commit()
    # avant migration : l'ancien CHECK rejette `review_findings`
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO alerts (id,project,feature_ref,feature,kind,severity,reason,status,"
                     "created_at,updated_at) VALUES ('a2','p','p/g','g','review_findings','info','r','open',"
                     "'t','t')")
    conn.rollback()

    assert store.migrate(conn) == schema.SCHEMA_VERSION                     # migre → v19 (rebuild de table)
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'").fetchone()[0]
    assert "review_findings" in ddl                                        # le CHECK a gagné le kind
    assert conn.execute("SELECT kind FROM alerts WHERE id='a1'").fetchone()[0] == "gate_red"   # préservé
    # les index CORRECTNESS survivent au rebuild (recréés dans la migration)
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' "
                                      "AND tbl_name='alerts'")}
    assert "ux_alerts_open" in idx and "ix_alerts_status" in idx
    # l'UPSERT d'`emit_alert` (ON CONFLICT sur ux_alerts_open) fonctionne pour le nouveau kind
    alerts.emit_alert(conn, project="p", feature_ref="p/g", feature="g", kind="review_findings",
                      reason="1 🟡", severity="info", findings=["🟡 x.ts:1 — foo"])
    row = conn.execute("SELECT severity,findings FROM alerts WHERE kind='review_findings'").fetchone()
    assert row[0] == "info" and "x.ts:1" in row[1]
    assert store.migrate(conn) == schema.SCHEMA_VERSION                     # idempotent
    conn.close()
