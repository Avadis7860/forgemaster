"""Tests de structure : garantir que la STRUCTURE tient (imports, parser, socle fonctionnel) pendant
qu'on porte les couches une par une. Le socle (config/core/db) n'est PAS un stub — il est prouvé ici.
Les couches hautes restent des stubs (NotImplementedError) — non testées à ce stade."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import cockpit
from cockpit.cli import build_parser
from cockpit.config import ENV_HOME, ENV_PROJECTS_ROOT, Settings
from cockpit.core import fs, ids, run
from cockpit.db import schema, store


def test_package_version():
    assert cockpit.__version__


# --- socle config : 3 modes de résolution (explicite / env / défaut) ------------------------------
def test_config_resolve_explicit(tmp_path: Path):
    s = Settings.resolve(home=tmp_path / "h", projects_root=tmp_path / "p")
    assert s.home == (tmp_path / "h").resolve()
    assert s.projects_root == (tmp_path / "p").resolve()
    assert s.db_path == (tmp_path / "h").resolve() / "cockpit.db"


def test_config_resolve_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_HOME, str(tmp_path / "eh"))
    monkeypatch.setenv(ENV_PROJECTS_ROOT, str(tmp_path / "ep"))
    s = Settings.resolve()
    assert s.home == (tmp_path / "eh").resolve()
    assert s.projects_root == (tmp_path / "ep").resolve()


def test_config_resolve_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(ENV_HOME, raising=False)
    monkeypatch.delenv(ENV_PROJECTS_ROOT, raising=False)
    s = Settings.resolve()
    assert s.home == (Path("~/.cockpit").expanduser().resolve())
    assert s.projects_root == (Path("~/projects").expanduser().resolve())


# --- socle core : exécution locale, slugs, chemin borné -------------------------------------------
def test_core_run_local():
    r = run.run(["printf", "%s", "hello"])
    assert r.ok
    assert r.stdout == "hello"


def test_core_run_check_raises_on_failure():
    with pytest.raises(run.RunError):
        run.run([sys.executable, "-c", "import sys; sys.exit(3)"], check=True)


def test_core_ids_slug():
    assert ids.is_slug("ma-feature-2")
    assert not ids.is_slug("Ma_Feature")
    assert not ids.is_slug("../evil")
    assert ids.ensure_slug("ok-slug") == "ok-slug"
    with pytest.raises(ValueError):
        ids.ensure_slug("bad slug")
    assert ids.new_id() != ids.new_id()


def test_core_fs_safe_path():
    root = "/srv/project"
    assert fs.safe_path("sub/file", root=root) == "/srv/project/sub/file"
    assert fs.safe_path("", root=root) == root
    with pytest.raises(ValueError):
        fs.safe_path("../../etc/passwd", root=root)


def test_core_fs_jsonl_roundtrip(tmp_path: Path):
    rows = [{"a": 1, "line": "avec séparateur"}, {"a": 2}]
    fp = tmp_path / "d.jsonl"
    fs.write_jsonl(fp, rows)
    assert fs.read_jsonl(fp) == rows  # U+2028 préservé (split sur \n, pas splitlines)


# --- socle db : le schéma se crée -----------------------------------------------------------------
def test_db_schema_creates_all_tables(tmp_path: Path):
    conn = store.connect(tmp_path / "cockpit.db")
    store.migrate(conn)
    names = sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))
    assert names == ["dispatch_jobs", "features", "port_reservations", "projects", "tasks"]
    assert schema.schema_version(conn) == schema.SCHEMA_VERSION


def test_db_open_db_from_settings(tmp_path: Path):
    s = Settings.resolve(home=tmp_path)
    conn = store.open_db(s)
    # FK actives (invariant dur du schéma)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# --- socle cli : le parser câble toutes les sous-commandes ----------------------------------------
def test_cli_parser_wires_all_subcommands():
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")  # noqa: SLF001 (introspection de test)
    assert set(sub.choices) == {
        "project", "roadmap", "task", "dispatch", "gate", "merge", "onboard", "serve",
    }


def test_cli_help_runs():
    r = run.run([sys.executable, "-m", "cockpit", "--help"])
    assert r.ok
    assert "cockpit" in r.stdout


# --- imports paresseux : le daemon s'importe SANS fastapi -----------------------------------------
def test_daemon_imports_without_fastapi():
    code = (
        "import sys; sys.modules['fastapi'] = None; sys.modules['uvicorn'] = None; "
        "import cockpit.daemon.app as a; print(hasattr(a, 'build_app') and hasattr(a, 'serve'))"
    )
    r = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ}
    )
    assert r.returncode == 0, r.stderr
    assert "True" in r.stdout
