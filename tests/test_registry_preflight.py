"""Préflight `create_project` — un type inconnu qui existe au miroir SoT local (donc AJOUTÉ après le build
de ce cockpit) donne un message de staleness actionnable au lieu du sec « type inconnu » ; sans miroir, le
message d'origine est intact (totalité : le préflight ne transforme jamais un 400 en 500). Git requis."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cockpit import build_provenance
from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.git.internal import writeback_env
from cockpit.projects import registry
from cockpit.provision import BundleError

_ENV = writeback_env(("Test", "test@example.invalid"), base={"PATH": os.environ.get("PATH", "")})
_TYPES = "src/cockpit/provision/bundles/types"


def _run(*args: str, cwd: Path) -> None:
    res = run.run(["git", *args], cwd=cwd, env=_ENV)
    assert res.ok, res.stderr


def _rev(ref: str, cwd: Path) -> str:
    res = run.run(["git", "rev-parse", ref], cwd=cwd, env=_ENV)
    assert res.ok, res.stderr
    return res.stdout.strip()


def _seed_cockpit_mirror(projects_root: Path, future_type: str) -> str:
    """Sème le miroir SoT bare de cockpit (`<projects_root>/cockpit/sot.git`) : commit1 = type `cli-tool`
    (déjà installé), commit2 ajoute `future_type`. Retourne le SHA de commit1 (le « build » périmé, avant le
    type)."""
    seed = projects_root / "_seed"
    (seed / _TYPES / "cli-tool").mkdir(parents=True)
    (seed / _TYPES / "cli-tool" / ".keep").write_text("", encoding="utf-8")
    _run("init", "-q", "-b", "main", cwd=seed)
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "c1", cwd=seed)
    sha1 = _rev("HEAD", cwd=seed)
    (seed / _TYPES / future_type).mkdir(parents=True)
    (seed / _TYPES / future_type / ".keep").write_text("", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "c2", cwd=seed)
    sot = projects_root / "cockpit" / "sot.git"
    sot.parent.mkdir(parents=True, exist_ok=True)
    res = run.run(["git", "clone", "--bare", "-q", str(seed), str(sot)], env=_ENV)
    assert res.ok, res.stderr
    return sha1


def _ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    settings.projects_root.mkdir(parents=True, exist_ok=True)
    return settings, store.open_db(settings)


def test_unknown_type_enriched_when_added_after_build(tmp_path: Path, monkeypatch):
    settings, conn = _ctx(tmp_path)
    try:
        sha1 = _seed_cockpit_mirror(settings.projects_root, future_type="zzz-future")
        stamp = tmp_path / "_build.json"                            # ce cockpit bâti AVANT zzz-future
        stamp.write_text(json.dumps({"sha": sha1, "committed_at": None}), encoding="utf-8")
        monkeypatch.setattr(build_provenance, "_STAMP", stamp)
        with pytest.raises(BundleError) as exc:
            registry.create_project(conn, settings, slug="proj", project_type="zzz-future")
        msg = str(exc.value)
        assert "zzz-future" in msg and "réinjecte" in msg.lower() and "retard" in msg
    finally:
        conn.close()


def test_unknown_type_plain_message_without_mirror(tmp_path: Path):
    settings, conn = _ctx(tmp_path)                                # aucun miroir cockpit local
    try:
        with pytest.raises(BundleError) as exc:
            registry.create_project(conn, settings, slug="proj", project_type="zzz-nonexistent")
        msg = str(exc.value)
        assert "zzz-nonexistent" in msg and "réinjecte" not in msg.lower()   # message d'origine, non enrichi
    finally:
        conn.close()
