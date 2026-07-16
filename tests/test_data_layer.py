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


def test_create_project_stamps_template_provenance_for_derived_type(ctx):
    """Un type dont le seed est **projeté d'un template corpus** (browser-game, cf. provision.derive) tamponne
    aussi `template@sha` — de quel template le seed a été dérivé au build. Trace la chaîne SoT-and-derive
    complète (template → seed → projet). Un type non dérivé (générique) n'a pas ces clés."""
    settings, conn = ctx
    import tomllib

    from cockpit.provision import derive
    registry.create_project(conn, settings, slug="void-runner", project_type="browser-game")
    sot = registry.sot_path_for(settings, "void-runner")
    stamp = tomllib.loads(
        run.run(["git", "-C", str(sot), "show", "dev:.cockpit/provenance.toml"]).stdout)["provenance"]
    ref, sha = derive.template_provenance("browser-game")
    assert stamp["template"] == ref == "browser-game-pve/scaffold"
    assert stamp["template_sha"] == sha
    # le générique n'est pas dérivé → tampon 3-clés, sans template
    registry.create_project(conn, settings, slug="plain")
    gen = tomllib.loads(run.run(["git", "-C", str(registry.sot_path_for(settings, "plain")),
                                 "show", "dev:.cockpit/provenance.toml"]).stdout)["provenance"]
    assert "template" not in gen


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
