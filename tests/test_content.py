"""Tests de la couche `content` — canal d'injection d'un asset uploadé par l'opérateur dans un projet.

Deux niveaux, à l'image de `test_design` : (1) unité sur `upload.write_project_upload` (écrit les bytes,
no-op sur data vide, bornes verrouillées : type/taille/secret/traversal) ; (2) intégration
`ingest.ingest_upload` sur un **SoT réel** (voie forge quand aucun worktree actif ; voie live dans un
worktree actif ; jamais de commit direct sur `dev` ; idempotence ; erreurs typées).

Spec : `docs/specs/project-content-upload.md`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgemaster.cli import _h_upload, build_parser
from forgemaster.config import Settings
from forgemaster.content import ingest, upload
from forgemaster.content.upload import _UPLOAD_MAX_BYTES
from forgemaster.daemon import app as app_mod
from forgemaster.db import store
from forgemaster.dispatch import worktree
from forgemaster.git.internal import InternalGit
from forgemaster.projects import registry
from forgemaster.projects.registry import sot_path_for
from forgemaster.roadmap import model


@pytest.fixture
def env(tmp_path: Path, fake_tools, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))               # isolé : git commit n'hérite d'aucun global
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    fake_tools(settings)
    yield settings, conn
    conn.close()


# -- unité : write_project_upload -------------------------------------------------------------------

def test_write_project_upload_writes_bytes_under_docs_design(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    target = upload.write_project_upload(wt, filename="logo.png", data=b"\x89PNG\r\n data")
    assert target is not None and target == wt / "docs" / "design" / "brand" / "logo.png"
    assert target.read_bytes() == b"\x89PNG\r\n data"        # lisible tel quel


def test_write_project_upload_honours_dest_slug(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    target = upload.write_project_upload(wt, filename="stamp.svg", data=b"<svg/>", dest_slug="schema")
    assert target == wt / "docs" / "design" / "schema" / "stamp.svg"


def test_write_project_upload_empty_data_is_noop(tmp_path: Path):
    """Data vide → aucun asset (symétrie avec `write_design_seed` sur brief blanc)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    assert upload.write_project_upload(wt, filename="logo.png", data=b"") is None
    assert not (wt / "docs" / "design").exists()             # rien écrit du tout


def test_write_project_upload_rejects_type_out_of_allowlist(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with pytest.raises(upload.UploadTypeRejected):
        upload.write_project_upload(wt, filename="payload.exe", data=b"MZ")
    assert not (wt / "docs" / "design").exists()


def test_write_project_upload_rejects_oversize_without_truncation(tmp_path: Path):
    """Taille > cap → lève (pointeur), jamais de troncature."""
    wt = tmp_path / "wt"
    wt.mkdir()
    big = b"x" * (upload._UPLOAD_MAX_BYTES + 1)
    with pytest.raises(upload.UploadTooLarge):
        upload.write_project_upload(wt, filename="huge.png", data=big)
    assert not (wt / "docs" / "design").exists()             # rien écrit (pas de fichier tronqué)


def test_write_project_upload_rejects_secret_even_with_allowed_ext(tmp_path: Path):
    """Un secret porte parfois une extension autorisée (`credentials.md`) → rejet (canal ≠ BWS)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    for name in ("credentials.md", ".env", "id_rsa", "server.pem", "tls.key"):
        with pytest.raises(upload.UploadRejected):
            upload.write_project_upload(wt, filename=name, data=b"secret")
    assert not (wt / "docs" / "design").exists()


@pytest.mark.parametrize("bad", ["../escape.png", "/abs/logo.png", "sub/logo.png", "..", "a\\b.png"])
def test_write_project_upload_rejects_path_traversal(tmp_path: Path, bad: str):
    wt = tmp_path / "wt"
    wt.mkdir()
    with pytest.raises(upload.UploadRejected):
        upload.write_project_upload(wt, filename=bad, data=b"x")


def test_write_project_upload_rejects_dest_slug_traversal(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with pytest.raises(upload.UploadRejected):
        upload.write_project_upload(wt, filename="logo.png", data=b"x", dest_slug="../secret")


# -- intégration : ingest_upload (SoT réel) ---------------------------------------------------------

def test_ingest_upload_forge_path_reserves_ephemeral_feature(env):
    """Aucun worktree actif → voie forge : feature `content-<x>` réservée, commit sur SA branche, le fichier
    est lisible dans le worktree, `merged=False`, et **rien n'est commité directement sur `dev`**."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    git = InternalGit()
    sot = sot_path_for(settings, "vr")
    dev_before = git.feature_sha(sot, "dev")

    report = ingest.ingest_upload(conn, settings, git, project="vr",
                                  filename="logo.png", data=b"\x89PNG brand")
    assert report["mode"] == "forge" and report["feature"] == "content-logo"
    assert report["branch"] == "feature/content-logo" and report["commit"] and report["merged"] is False
    # fichier lisible dans le worktree réservé
    asset = Path(report["file"])
    assert asset == Path(report["path"]) / "docs" / "design" / "brand" / "logo.png"
    assert asset.read_bytes() == b"\x89PNG brand"
    # feature éphémère active en base
    assert "content-logo" in {f["slug"] for f in model.list_features(conn, "vr")}
    # invariant fail-closed : dev n'a PAS bougé (jamais de commit direct sur dev)
    assert git.feature_sha(sot, "dev") == dev_before


def test_ingest_upload_live_path_writes_into_active_worktree(env):
    """Worktree actif présent (feature réservée) → voie live : écrit DEDANS + commit sur sa branche."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")       # crée déjà la feature `socle` (planned)
    git = InternalGit()
    res = worktree.reserve(conn, settings, git, project="vr", feature="socle")   # → status active + worktree

    report = ingest.ingest_upload(conn, settings, git, project="vr",
                                  filename="charte.pdf", data=b"%PDF brand")
    assert report["mode"] == "live" and report["feature"] == "socle"
    assert report["branch"] == "feature/socle" and report["path"] == str(res["path"])
    asset = res["path"] / "docs" / "design" / "brand" / "charte.pdf"
    assert asset.read_bytes() == b"%PDF brand"               # Read live dans le worktree de l'interview
    assert git.current_branch(res["path"]) == "feature/socle" and report["commit"]


def test_ingest_upload_is_idempotent_on_identical_reupload(env):
    """Ré-upload identique → commit no-op (arbre net → `commit_worktree` retourne None)."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    git = InternalGit()
    r1 = ingest.ingest_upload(conn, settings, git, project="vr", filename="logo.png", data=b"same")
    r2 = ingest.ingest_upload(conn, settings, git, project="vr", filename="logo.png", data=b"same")
    assert r1["commit"] and r2["commit"] is None             # rien de neuf à committer la 2e fois
    feats = [f for f in model.list_features(conn, "vr") if f["slug"] == "content-logo"]
    assert len(feats) == 1                                   # une seule feature éphémère (réutilisée)


def test_ingest_upload_empty_data_is_noop_without_side_effects(env):
    """Data vide → mode noop, aucune feature créée, aucun worktree réservé."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    report = ingest.ingest_upload(conn, settings, project="vr", filename="logo.png", data=b"")
    assert report["mode"] == "noop" and report["file"] is None and report["commit"] is None
    # aucune feature éphémère `content-<x>` réservée (le `socle` auto-créé au projet ne compte pas)
    assert not [f for f in model.list_features(conn, "vr") if f["slug"].startswith("content-")]


def test_ingest_upload_unknown_project_raises_keyerror(env):
    settings, conn = env
    with pytest.raises(KeyError):
        ingest.ingest_upload(conn, settings, project="ghost", filename="logo.png", data=b"x")


def test_ingest_upload_unknown_explicit_feature_raises_keyerror(env):
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    with pytest.raises(KeyError):
        ingest.ingest_upload(conn, settings, project="vr", filename="logo.png",
                             data=b"x", feature="ghost")


# -- parité route HTTP : POST /api/projects/{slug}/upload -------------------------------------------

def _client(settings: Settings) -> TestClient:
    return TestClient(app_mod.build_app(settings))


def test_route_upload_forge_path_creates_content_feature(env):
    """`POST …/upload` (multipart) sans worktree actif → 201, voie forge, asset lisible sous docs/design."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    r = _client(settings).post("/api/projects/vr/upload",
                               files={"file": ("logo.png", b"\x89PNG brand", "image/png")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mode"] == "forge" and body["feature"] == "content-logo" and body["merged"] is False
    assert Path(body["file"]).read_bytes() == b"\x89PNG brand"
    assert "content-logo" in {f["slug"] for f in model.list_features(conn, "vr")}


def test_route_upload_honours_dest_form_field(env):
    """Le champ Form `dest` route l'asset sous docs/design/<dest>/ (parité du `dest_slug` du cœur)."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    r = _client(settings).post("/api/projects/vr/upload",
                               files={"file": ("stamp.svg", b"<svg/>", "image/svg+xml")},
                               data={"dest": "schema"})
    assert r.status_code == 201, r.text
    assert Path(r.json()["file"]).parts[-3:] == ("design", "schema", "stamp.svg")


def test_route_upload_live_path_writes_into_active_worktree(env):
    """Worktree actif réservé → voie live via HTTP : écrit dedans + commit sur sa branche (Read live)."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    res = worktree.reserve(conn, settings, InternalGit(), project="vr", feature="socle")
    r = _client(settings).post("/api/projects/vr/upload",
                               files={"file": ("charte.pdf", b"%PDF brand", "application/pdf")})
    assert r.status_code == 201, r.text
    assert r.json()["mode"] == "live" and r.json()["feature"] == "socle"
    assert (res["path"] / "docs" / "design" / "brand" / "charte.pdf").read_bytes() == b"%PDF brand"


def test_route_upload_empty_file_is_noop(env):
    """Fichier vide → 201 mode noop, aucune feature créée (symétrie du cœur)."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    r = _client(settings).post("/api/projects/vr/upload",
                               files={"file": ("logo.png", b"", "image/png")})
    assert r.status_code == 201, r.text
    assert r.json()["mode"] == "noop" and r.json()["file"] is None
    assert not [f for f in model.list_features(conn, "vr") if f["slug"].startswith("content-")]


def test_route_upload_rejected_type_maps_to_415(env):
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    r = _client(settings).post("/api/projects/vr/upload",
                               files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415, r.text


def test_route_upload_too_large_maps_to_413(env):
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    payload = b"\x00" * (_UPLOAD_MAX_BYTES + 1)
    r = _client(settings).post("/api/projects/vr/upload",
                               files={"file": ("big.png", payload, "image/png")})
    assert r.status_code == 413, r.text


def test_route_upload_secret_maps_to_400(env):
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    r = _client(settings).post("/api/projects/vr/upload",
                               files={"file": ("credentials.md", b"token=xyz", "text/markdown")})
    assert r.status_code == 400, r.text


def test_route_upload_unknown_project_maps_to_404(env):
    settings, _ = env
    r = _client(settings).post("/api/projects/ghost/upload",
                               files={"file": ("logo.png", b"x", "image/png")})
    assert r.status_code == 404, r.text


# -- parité CLI : forgemaster upload <projet> <chemin> --------------------------------------------------

def test_cli_upload_delegates_to_same_core(env, tmp_path: Path):
    """`forgemaster upload` lit le fichier local et délègue au **même** cœur que la route (même
    destination)."""
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    src = tmp_path / "logo.png"
    src.write_bytes(b"\x89PNG cli")
    args = build_parser().parse_args(["upload", "vr", str(src)])
    assert _h_upload(settings, args) == 0
    feats = [f for f in model.list_features(conn, "vr") if f["slug"] == "content-logo"]
    assert len(feats) == 1                                    # même feature éphémère que la route


def test_cli_upload_honours_dest_flag(env, tmp_path: Path):
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    src = tmp_path / "stamp.svg"
    src.write_bytes(b"<svg/>")
    args = build_parser().parse_args(["upload", "vr", str(src), "--dest", "schema"])
    assert _h_upload(settings, args) == 0


def test_cli_upload_unreadable_file_returns_1(env, tmp_path: Path):
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    args = build_parser().parse_args(["upload", "vr", str(tmp_path / "missing.png")])
    assert _h_upload(settings, args) == 1                     # fichier illisible → code 1, pas d'exception


def test_cli_upload_rejected_type_returns_1(env, tmp_path: Path):
    settings, conn = env
    registry.create_project(conn, settings, slug="vr")
    src = tmp_path / "evil.exe"
    src.write_bytes(b"MZ")
    args = build_parser().parse_args(["upload", "vr", str(src)])
    assert _h_upload(settings, args) == 1                     # borne type remonte → code 1
