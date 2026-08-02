"""Tests de `provision.reseed` (+ la primitive git `overlay_commit`) : re-matérialiser les fichiers
scaffold-owned d'un projet EXISTANT dans son SoT (`dev`) en préservant le travail worker.

Invariants prouvés : owned mis à jour ↔ contenu du bundle ; chemins worker (non-owned) **intacts** ;
**idempotent** (rien à jour → aucun commit, `dev` n'avance pas) ; `main`/arbre existant **intouchés** ;
fail-closed (type sans `reseed_owned` → ValueError ; projet absent → KeyError).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cockpit.config import Settings
from cockpit.core import run
from cockpit.daemon import app as app_mod
from cockpit.db import store
from cockpit.git.internal import InternalGit
from cockpit.projects import registry
from cockpit.provision import load_bundle, read_reseed_owned, reseed

_ID = ("t", "t@e.invalid")
# Contrat de RUN (infra scaffold) + de QUALITÉ (discipline des facettes, jamais éditée par le worker). Les
# GARDES de test semées ne sont PAS owned (la garde worker est plus riche/produit — l'écraser perdrait de la
# couverture).
_OWNED = {
    "Dockerfile", "compose.yaml", "nginx.conf", ".dockerignore",
    ".claude/facets/frontend/METHOD.md", ".claude/facets/content/METHOD.md",
    ".claude/facets/deploy/METHOD.md",
}
# La liste possédée de CHAQUE type, en dur : ce tableau EST le contrat de préservation. Une liste par type
# parce qu'un overlay surcharge `bundle.toml` en whole-file — un bloc mis dans la base ne serait pas hérité.
# Deux exclusions structurantes s'y lisent en creux :
#   • `front-ts` / `service-api` n'ont PAS leur `Dockerfile` owned — c'est un STUB soudé à son entrypoint
#     (`CMD node server.mjs` / `CMD python app.py`), que le worker DOIT réécrire pour un vrai déploiement ;
#     le posséder écraserait son travail. Ce qui est stable, c'est le contrat de port → `compose.yaml`.
#   • `cli-tool` n'a aucun contrat de RUN : un outil CLI ne se sert pas (ni Dockerfile ni compose dans son
#     overlay). Sa liste est courte parce que sa surface possédée l'est, pas par omission.
_OWNED_BY_TYPE: dict[str, set[str]] = {
    "browser-game": {
        "Dockerfile", "compose.yaml", ".dockerignore",
        ".claude/facets/backend/METHOD.md", ".claude/facets/frontend/METHOD.md",
        ".claude/facets/game-design/METHOD.md",
    },
    "cli-tool": {".claude/facets/tool/METHOD.md"},
    "front-ts": {
        "compose.yaml", ".dockerignore",
        ".claude/facets/backend/METHOD.md", ".claude/facets/frontend/METHOD.md",
    },
    "service-api": {
        "compose.yaml", ".dockerignore", ".claude/facets/backend/METHOD.md",
    },
    "site-vitrine": _OWNED,
}


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


def _show(sot: Path, ref_path: str) -> str:
    return run.run(["git", "-C", str(sot), "show", ref_path]).stdout


def _sha(sot: Path, branch: str) -> str:
    return run.run(["git", "-C", str(sot), "rev-parse", branch]).stdout.strip()


def _vitrine(ctx) -> tuple[Settings, object, Path]:
    settings, conn = ctx
    registry.create_project(conn, settings, slug="viti", project_type="site-vitrine")
    return settings, conn, registry.sot_path_for(settings, "viti")


def test_read_reseed_owned_is_run_plus_quality_contract_for_vitrine_empty_for_generic():
    assert set(read_reseed_owned("site-vitrine")) == _OWNED
    assert read_reseed_owned("generic") == []


@pytest.mark.parametrize("project_type", sorted(_OWNED_BY_TYPE))
def test_every_typed_bundle_declares_its_owned_contract(project_type: str):
    """AUCUN type semable n'est muet : sans `reseed_owned`, un fix de scaffold ne rejoint JAMAIS les projets
    existants de ce type — la préservation ne couvrirait presque rien. Le contenu exact vaut contrat."""
    assert set(read_reseed_owned(project_type)) == _OWNED_BY_TYPE[project_type]


@pytest.mark.parametrize("project_type", sorted(_OWNED_BY_TYPE))
def test_owned_paths_exist_in_composed_bundle(project_type: str):
    """On ne peut posséder un fichier qu'on ne sème pas : chaque chemin owned est présent dans le bundle
    composé `base ⊕ overlay`. (Doublon volontaire de `validate_bundle` : ici l'erreur nomme le type ET le
    chemin, ce qui est ce qu'on lit quand un renommage d'overlay casse une liste.)"""
    bundle = load_bundle(project_type)
    for path in sorted(_OWNED_BY_TYPE[project_type]):
        assert path in bundle, f"{project_type}: reseed_owned={path!r} absent du bundle composé"


@pytest.mark.parametrize("project_type", sorted(_OWNED_BY_TYPE))
def test_seeded_test_guards_are_never_owned(project_type: str):
    """Exclusion explicite : une garde semée (`*.test.ts`) n'est JAMAIS owned. La garde d'un worker est plus
    riche et propre à son produit — l'écraser au reseed retirerait de la couverture."""
    assert not [p for p in _OWNED_BY_TYPE[project_type] if p.endswith(".test.ts")]


@pytest.mark.parametrize("project_type", sorted(_OWNED_BY_TYPE))
def test_reseed_updates_owned_preserves_worker_and_is_idempotent_for_every_type(ctx, project_type: str):
    """La preuve FONCTIONNELLE, par type : sur un projet réel, (a) les owned sont re-matérialisés au contenu
    du bundle, (b) un fichier worker modifié à côté est INTACT, (c) un second appel ne commite rien."""
    settings, conn = ctx
    slug = project_type.replace("-", "")
    registry.create_project(conn, settings, slug=slug, project_type=project_type)
    sot = registry.sot_path_for(settings, slug)
    owned = sorted(_OWNED_BY_TYPE[project_type])
    stale, worker_path = owned[0], "docs/architecture.md"          # non-owned dans TOUS les types
    InternalGit().overlay_commit(
        sot, branch="dev", identity=_ID, message="setup: scaffold périmé + travail worker",
        files={stale: "# ANCIEN scaffold périmé\n", worker_path: "# ÉDITION WORKER — à préserver\n"})
    worker_before = _show(sot, f"dev:{worker_path}")

    report = reseed.reseed_project(conn, settings, project=slug)

    assert report["updated"] is True and report["commit"] is not None
    assert set(report["files"]) == set(owned)
    bundle = load_bundle(project_type)
    for path in owned:
        assert _show(sot, f"dev:{path}") == bundle[path]
    assert _show(sot, f"dev:{worker_path}") == worker_before
    assert reseed.reseed_project(conn, settings, project=slug)["updated"] is False   # idempotent


def test_reseed_updates_owned_and_preserves_worker(ctx):
    settings, conn, sot = _vitrine(ctx)
    git = InternalGit()
    # Simule un projet réel : Dockerfile PÉRIMÉ (l'ancienne version cassée) + travail worker sur un chemin
    # NON-owned (une page éditée). Les deux sur `dev`.
    git.overlay_commit(sot, branch="dev", identity=_ID, message="setup: scaffold périmé + travail worker",
                       files={"Dockerfile": "FROM scratch\n# ANCIEN scaffold cassé (npm ci sans lockfile)\n",
                              "web/src/pages/index.astro": "<!-- ÉDITION WORKER — à préserver -->\n"})
    worker_before = _show(sot, "dev:web/src/pages/index.astro")

    report = reseed.reseed_project(conn, settings, project="viti")

    assert report["updated"] is True and report["commit"] is not None
    assert report["project_type"] == "site-vitrine" and report["branch"] == "dev"
    assert set(report["files"]) == _OWNED
    # owned re-matérialisés au contenu EXACT du bundle (dont le fix du Dockerfile)
    bundle = load_bundle("site-vitrine")
    for path in _OWNED:
        assert _show(sot, f"dev:{path}") == bundle[path]
    assert "if [ -f package-lock.json ]" in _show(sot, "dev:Dockerfile")   # le fix de build
    # travail worker (chemin non-owned) INTACT
    assert _show(sot, "dev:web/src/pages/index.astro") == worker_before


def test_reseed_is_idempotent_no_commit_when_up_to_date(ctx):
    settings, conn, sot = _vitrine(ctx)
    dev_before = _sha(sot, "dev")   # projet frais : owned déjà == bundle

    report = reseed.reseed_project(conn, settings, project="viti")

    assert report["updated"] is False and report["commit"] is None
    assert _sha(sot, "dev") == dev_before   # la ref N'A PAS avancé (aucun commit vide)
    # re-jouer reste un no-op
    assert reseed.reseed_project(conn, settings, project="viti")["updated"] is False


def test_reseed_advances_dev_only_not_main(ctx):
    settings, conn, sot = _vitrine(ctx)
    git = InternalGit()
    git.overlay_commit(sot, branch="dev", identity=_ID, message="setup: périmé",
                       files={"Dockerfile": "FROM scratch\n# périmé\n"})
    main_before, dev_before = _sha(sot, "main"), _sha(sot, "dev")

    reseed.reseed_project(conn, settings, project="viti")

    assert _sha(sot, "main") == main_before   # `main` JAMAIS touché
    assert _sha(sot, "dev") != dev_before      # `dev` seul avance


def test_reseed_rejects_type_without_owned(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="plain", project_type="generic")
    with pytest.raises(ValueError, match="reseed_owned"):
        reseed.reseed_project(conn, settings, project="plain")


def test_reseed_unknown_project_raises_keyerror(ctx):
    settings, conn = ctx
    with pytest.raises(KeyError):
        reseed.reseed_project(conn, settings, project="absent")


def test_overlay_commit_preserves_other_paths_and_is_idempotent(ctx):
    settings, conn, sot = _vitrine(ctx)
    git = InternalGit()
    nginx_before = _show(sot, "dev:nginx.conf")   # un fichier NON touché par l'overlay
    sha1 = git.overlay_commit(sot, branch="dev", identity=_ID, message="overlay: un sous-dossier",
                              files={"web/src/pages/index.astro": "<!-- overlay -->\n"})
    assert sha1 is not None
    assert _show(sot, "dev:web/src/pages/index.astro") == "<!-- overlay -->\n"
    assert _show(sot, "dev:nginx.conf") == nginx_before   # préservé par construction
    # même overlay, contenu identique → idempotent (aucun commit, None)
    assert git.overlay_commit(sot, branch="dev", identity=_ID, message="overlay: idem",
                              files={"web/src/pages/index.astro": "<!-- overlay -->\n"}) is None


def test_overlay_commit_rejects_empty_and_missing_branch(ctx):
    settings, conn, sot = _vitrine(ctx)
    git = InternalGit()
    with pytest.raises(Exception, match="aucun fichier"):
        git.overlay_commit(sot, branch="dev", files={}, message="vide", identity=_ID)
    with pytest.raises(Exception, match="absente"):
        git.overlay_commit(sot, branch="nope", files={"a": "b"}, message="x", identity=_ID)


def test_reseed_targets_feature_branch_preserving_work(ctx):
    settings, conn, sot = _vitrine(ctx)
    git = InternalGit()
    # feature en vol : branche feature/design ancrée sur dev, avec Dockerfile PÉRIMÉ + travail worker (web/).
    run.run(["git", "-C", str(sot), "branch", "feature/design", "dev"])
    git.overlay_commit(sot, branch="feature/design", identity=_ID, message="worker: travail + scaffold",
                       files={"Dockerfile": "FROM scratch\n# court/périmé\n",
                              "web/src/pages/x.astro": "<!-- WORK -->\n"})
    dev_before = _sha(sot, "dev")
    worker_before = _show(sot, "feature/design:web/src/pages/x.astro")

    report = reseed.reseed_project(conn, settings, project="viti", branch="feature/design")

    assert report["updated"] is True and report["branch"] == "feature/design"
    bundle = load_bundle("site-vitrine")
    for path in _OWNED:                                              # owned re-semés au contenu du bundle
        assert _show(sot, f"feature/design:{path}") == bundle[path]
    assert _show(sot, "feature/design:web/src/pages/x.astro") == worker_before   # travail worker préservé
    assert _sha(sot, "dev") == dev_before                            # `dev` (et main) INTOUCHÉ


def test_overlay_commit_syncs_live_worktree_files_not_just_the_ref(ctx):
    """RÉGRESSION (bug drain avagency 2026-07-29) : `overlay_commit` avançait le REF de branche par plumbing
    mais laissait le WORKTREE vivant checké-out dessus périmé — HEAD bougeait sous ses pieds, index+arbre de
    travail restaient l'ancien contenu. `deploy_preview` bâtit depuis les FICHIERS du worktree → il servait
    l'ancien nginx (fuite du port interne persistante malgré le reseed), et les chemins owned apparaissaient
    comme des « modifs worker » fantômes. La correction resynchronise les chemins overlayés du worktree."""
    settings, conn, sot = _vitrine(ctx)
    git = InternalGit()
    wt = settings.projects_root / "wt-design"
    git.add_worktree(sot, wt, branch="feature/design", base="dev")   # worktree VIVANT sorti sur la feature
    # nginx.conf périmé (sans le fix) matérialisé dans le worktree, comme un seed d'origine cassé.
    (wt / "nginx.conf").write_text("server { listen 8000; }\n# PÉRIMÉ : pas de absolute_redirect off\n")
    git.commit_worktree(wt, message="worker: état initial", identity=_ID)

    report = reseed.reseed_project(conn, settings, project="viti", branch="feature/design")

    assert report["updated"] is True
    bundle = load_bundle("site-vitrine")
    # (1) le FICHIER SUR DISQUE du worktree (pas seulement l'arbre committé) porte le contrat corrigé —
    #     c'est lui que `deploy_preview` bâtit.
    assert (wt / "nginx.conf").read_text() == bundle["nginx.conf"]
    assert "absolute_redirect off" in (wt / "nginx.conf").read_text()
    # (2) le worktree est PROPRE sur les chemins owned : aucune « modif fantôme » staged qu'un commit worker
    #     ultérieur ré-annulerait.
    st = git.status(wt)
    assert all(f["path"] != "nginx.conf" for f in st["files"]), st["files"]


# -- HTTP (route thin, auth-free, mêmes garde-fous que l'op) -----------------------------------------

def test_route_reseed_updates_and_reports(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="viti", project_type="site-vitrine")
    sot = registry.sot_path_for(settings, "viti")
    conn.close()
    InternalGit().overlay_commit(sot, branch="dev", identity=_ID, message="setup: périmé",
                                 files={"Dockerfile": "FROM scratch\n# périmé\n"})
    client = TestClient(app_mod.build_app(settings))
    resp = client.post("/api/projects/viti/scaffold/reseed")   # pas de gate d'auth (mutation locale)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] is True and set(body["files"]) == _OWNED
    assert "if [ -f package-lock.json ]" in _show(sot, "dev:Dockerfile")


def test_route_reseed_404_on_absent_project(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    client = TestClient(app_mod.build_app(settings))
    assert client.post("/api/projects/absent/scaffold/reseed").status_code == 404


def test_route_reseed_400_on_type_without_owned(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="plain", project_type="generic")
    conn.close()
    client = TestClient(app_mod.build_app(settings))
    assert client.post("/api/projects/plain/scaffold/reseed").status_code == 400
