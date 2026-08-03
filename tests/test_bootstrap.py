"""Tests de l'amorçage (`cockpit bootstrap`) : manifeste d'outils → adoption idempotente via P1, sur une
DB + projects_root + COCKPIT_HOME jetables. Upstreams = vrais bares locaux (zéro réseau)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest
import yaml

from cockpit import bootstrap
from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.projects import registry
from cockpit.secrets import build_store

_GIT_ENV = {"PATH": os.environ.get("PATH", ""),
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}


def _make_upstream(path: Path, *, default: str = "main", extra: tuple[str, ...] = ("dev",)) -> Path:
    """Vrai repo git local (upstream d'adoption) avec du contenu réel + branches — cf. test_data_layer."""
    path.mkdir(parents=True)
    run.run(["git", "init", "-q", "-b", default, str(path)], env=_GIT_ENV)
    (path / "README.md").write_text("# vrai contenu\n", encoding="utf-8")
    run.run(["git", "-C", str(path), "add", "-A"], env=_GIT_ENV)
    run.run(["git", "-C", str(path), "commit", "-q", "-m", "real work"], env=_GIT_ENV)
    for b in extra:
        run.run(["git", "-C", str(path), "branch", b], env=_GIT_ENV)
    return path


def _write_manifest(settings: Settings, tools: list[dict]) -> Path:
    settings.home.mkdir(parents=True, exist_ok=True)
    path = bootstrap.manifest_path(settings)
    path.write_text(yaml.safe_dump({"tools": tools}), encoding="utf-8")
    return path


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


def _args(**kw) -> argparse.Namespace:
    base = {"init": False, "token_file": None}
    base.update(kw)
    return argparse.Namespace(**base)


# -- happy path : les outils sont adoptés, classés kind=tool, avec leur VRAI contenu -----------------

def test_run_bootstrap_adopts_tools_as_kind_tool(ctx, tmp_path):
    settings, conn = ctx
    up1 = _make_upstream(tmp_path / "u-codemap")
    up2 = _make_upstream(tmp_path / "u-docsmap")
    _write_manifest(settings, [{"slug": "code-map", "source_url": str(up1)},
                               {"slug": "docs-map", "source_url": str(up2)}])
    manifest = bootstrap.load_manifest(settings)
    report = bootstrap.run_bootstrap(conn, settings, manifest=manifest)
    assert report["created"] == ["code-map", "docs-map"]
    assert report["skipped"] == [] and report["failed"] == []
    # partitionnés « Outils » : les deux portent kind=tool (rail section Outils)
    tools = [p for p in registry.list_projects(conn) if p["kind"] == "tool"]
    assert {p["slug"] for p in tools} == {"code-map", "docs-map"}
    # VRAI contenu cloné (≠ seed), miroir câblé sur la source (D7)
    sot = registry.sot_path_for(settings, "code-map")
    names = run.run(["git", "-C", str(sot), "ls-tree", "-r", "--name-only", "dev"]).stdout.split()
    assert "README.md" in names and "CLAUDE.md" not in names
    assert registry.get_project(conn, "code-map")["mirror_remote"] == str(up1)


def test_run_bootstrap_is_idempotent(ctx, tmp_path):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u")
    _write_manifest(settings, [{"slug": "code-map", "source_url": str(up)}])
    manifest = bootstrap.load_manifest(settings)
    first = bootstrap.run_bootstrap(conn, settings, manifest=manifest)
    second = bootstrap.run_bootstrap(conn, settings, manifest=manifest)
    assert first["created"] == ["code-map"]
    assert second["created"] == [] and second["skipped"] == ["code-map"]   # ré-exécution = no-op
    assert len([p for p in registry.list_projects(conn) if p["slug"] == "code-map"]) == 1  # pas de doublon


def test_run_bootstrap_skips_preexisting_slug(ctx, tmp_path):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="cockpit")   # déjà là (seed), pas ré-adopté
    up = _make_upstream(tmp_path / "u")
    _write_manifest(settings, [{"slug": "cockpit", "source_url": str(up)},
                               {"slug": "code-map", "source_url": str(up)}])
    report = bootstrap.run_bootstrap(conn, settings, manifest=bootstrap.load_manifest(settings))
    assert report["skipped"] == ["cockpit"] and report["created"] == ["code-map"]


# -- une entrée en échec n'avorte pas les autres (best-effort par entrée) ----------------------------

def test_run_bootstrap_bad_url_is_isolated_failure(ctx, tmp_path):
    settings, conn = ctx
    good = _make_upstream(tmp_path / "u-good")
    _write_manifest(settings, [{"slug": "ok-tool", "source_url": str(good)},
                               {"slug": "ghost", "source_url": str(tmp_path / "n-existe-pas")}])
    report = bootstrap.run_bootstrap(conn, settings, manifest=bootstrap.load_manifest(settings))
    assert report["created"] == ["ok-tool"]
    assert [f["slug"] for f in report["failed"]] == ["ghost"]
    assert not registry.sot_path_for(settings, "ghost").exists()   # pas de row/SoT orphelin


# -- manifeste : absent = no-op ; invalide = abort loud ---------------------------------------------

def test_load_manifest_absent_returns_none(ctx):
    settings, _ = ctx
    assert bootstrap.load_manifest(settings) is None   # aucun fichier → install générique


@pytest.mark.parametrize("blob", ["nope", "tools: 3", "tools:\n  - 42",
                                  "tools:\n  - source_url: https://x"])   # sans slug
def test_load_manifest_invalid_raises(ctx, blob):
    settings, _ = ctx
    settings.home.mkdir(parents=True, exist_ok=True)
    bootstrap.manifest_path(settings).write_text(blob, encoding="utf-8")
    with pytest.raises(ValueError, match="manifeste"):
        bootstrap.load_manifest(settings)


# -- 0 secret en clair : le token brut n'atterrit dans AUCUNE colonne ; credential_ref opaque -------

def test_bootstrap_never_persists_plaintext_token(ctx, tmp_path):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u")
    token = "ghp_SUPERSECRET_read_token_value"
    ref = build_store(settings).put(token, label="test")   # store = seul dépositaire de la valeur
    _write_manifest(settings, [{"slug": "priv-tool", "source_url": str(up), "credential_ref": ref}])
    bootstrap.run_bootstrap(conn, settings, manifest=bootstrap.load_manifest(settings))
    row = registry.get_project(conn, "priv-tool")
    assert row["credential_ref"] == ref                     # la DB ne porte que la RÉFÉRENCE opaque
    assert token not in " ".join(str(v) for v in row.values())   # jamais la valeur, dans aucune colonne


def test_preview_reports_available_and_adopted(ctx, tmp_path):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u")
    _write_manifest(settings, [{"slug": "code-map", "source_url": str(up)},
                               {"slug": "docs-map", "source_url": str(up)}])
    before = bootstrap.preview(conn, settings)
    assert before["available"] and before["total"] == 2 and before["adopted"] == 0
    bootstrap.run_bootstrap(conn, settings, manifest=bootstrap.load_manifest(settings))
    after = bootstrap.preview(conn, settings)
    assert after["adopted"] == 2
    assert all(t["adopted"] for t in after["tools"])
    # manifeste absent → aperçu non disponible (wizard générique)
    bootstrap.manifest_path(settings).unlink()
    assert bootstrap.preview(conn, settings)["available"] is False


# -- write_template + cli_dispatch (bout-en-bout : --init puis amorçage) ------------------------------

def test_write_template_no_overwrite(ctx):
    settings, _ = ctx
    path = bootstrap.write_template(settings)
    assert path.exists() and "AUCUN secret" in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="ne pas écraser"):
        bootstrap.write_template(settings)   # garde : ne jamais clobber une conf existante


def test_cli_dispatch_init_is_noop_bootstrap(ctx):
    settings, _ = ctx
    assert bootstrap.cli_dispatch(settings, _args(init=True)) == 0    # écrit le gabarit
    # le gabarit pointe des OWNER/… inexistants → un amorçage réel échouerait ; ici on prouve juste
    # qu'un manifeste absent AVANT --init donnait un no-op propre :
    settings2 = Settings.resolve(home=settings.home.parent / "vide", projects_root=settings.projects_root)
    assert bootstrap.cli_dispatch(settings2, _args()) == 0            # aucun manifeste → no-op, code 0


def test_cli_dispatch_token_file_stores_ref_not_plaintext(ctx, tmp_path):
    settings, conn = ctx
    up = _make_upstream(tmp_path / "u")
    _write_manifest(settings, [{"slug": "priv-tool", "source_url": str(up)}])
    tok = tmp_path / "tok.txt"
    tok.write_text("ghp_via_token_file\n", encoding="utf-8")
    assert bootstrap.cli_dispatch(settings, _args(token_file=str(tok))) == 0
    row = registry.get_project(conn, "priv-tool")
    assert row["credential_ref"] and "ghp_via_token_file" not in str(row["credential_ref"])


# -- l'édition maintainer livrée (deploy/bootstrap.yaml) reste chargeable par le vrai loader --------
# Gate-protège la DONNÉE d'édition : la recette P3 dépose CE fichier tel quel sous COCKPIT_HOME ; s'il
# dérivait vers un manifeste invalide, une install fraîche casserait. Le test le fait relire par le loader.

def test_shipped_deploy_manifest_is_valid_five_tools(ctx):
    settings, _ = ctx
    shipped = Path(__file__).resolve().parents[1] / "deploy" / "bootstrap.yaml"
    settings.home.mkdir(parents=True, exist_ok=True)
    bootstrap.manifest_path(settings).write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
    entries = bootstrap.load_manifest(settings)
    assert entries is not None
    slugs = {e["slug"] for e in entries}
    # les 5 frères (D1)
    assert slugs == {"cockpit", "code-map", "front-map", "docs-map", "forgemaster-catalogs"}
    for e in entries:
        assert e["kind"] == "tool"                                   # → rail « Outils »
        assert e["source_url"].startswith("https://github.com/") and e["source_url"].endswith(".git")
        assert e["credential_ref"] is None                           # forward-compat public (D7)
    # invariant « donnée, pas secret » : le fichier livré ne porte aucun token
    raw = shipped.read_text(encoding="utf-8")
    assert "ghp_" not in raw and "github_pat_" not in raw
