"""registry — CRUD des projets (create/list/get) sur la table `projects`. Un projet = identité durable :
`slug`, `sot_path` (bare local co-localisé), `mirror_remote` (best-effort), `backend` (internal|github).

Port : `services/aggregator/routers/projects.py` (registre). Refactor #1 (reçoit `settings` + connexion,
zéro module-global) / #4 (SoT sous `settings.projects_root`, aucun chemin d'hôte en dur).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cockpit.config import Settings
from cockpit.core import ids
from cockpit.db import store
from cockpit.git.internal import InternalGit
from cockpit.provision import load_payload

_COLS = ("id", "slug", "name", "sot_path", "mirror_remote", "backend", "created_at")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def sot_path_for(settings: Settings, slug: str) -> Path:
    """Chemin du SoT bare d'un projet : `<projects_root>/<slug>/sot.git`. Déterministe, sous config."""
    return settings.projects_root / slug / "sot.git"


def create_project(conn: sqlite3.Connection, settings: Settings, *,
                   slug: str, name: str | None = None, mirror_remote: str | None = None) -> dict:
    """Crée un projet (row DB) et **initialise son SoT bare local** (idempotent), semé du « toolkit
    auto-travaillable » (CLAUDE.md mince + `.docsmap.toml` + stub `docs/` + skills work-loop/quality-gate +
    settings) → chaque projet naît auto-travaillable seul. Lève `ValueError` si slug invalide, `KeyError`
    (via IntegrityError → ValueError) si le slug existe déjà."""
    ids.ensure_slug(slug, field="project")
    sot = sot_path_for(settings, slug)
    row = {"id": ids.new_id(), "slug": slug, "name": name or slug, "sot_path": str(sot),
           "mirror_remote": mirror_remote, "backend": "internal", "created_at": _now()}
    try:
        conn.execute(
            "INSERT INTO projects (id, slug, name, sot_path, mirror_remote, backend, created_at) "
            "VALUES (:id, :slug, :name, :sot_path, :mirror_remote, :backend, :created_at)", row)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"projet déjà existant : {slug!r}") from exc
    InternalGit().init_sot(sot, payload=load_payload())
    return row


def list_projects(conn: sqlite3.Connection) -> list[dict]:
    """Tous les projets, triés par slug."""
    return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY slug")]


def get_project(conn: sqlite3.Connection, slug: str) -> dict:
    """Un projet par slug, ou `KeyError` s'il n'existe pas."""
    r = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    if r is None:
        raise KeyError(slug)
    return dict(r)


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit project <action>` (create|list|get)."""
    conn = store.open_db(settings)
    try:
        if args.action == "create":
            p = create_project(conn, settings, slug=args.slug, name=args.name)
            print(f"projet créé : {p['slug']} — SoT {p['sot_path']}")
        elif args.action == "list":
            for p in list_projects(conn):
                print(f"{p['slug']}\t{p['backend']}\t{p['name']}")
        elif args.action == "get":
            print(json.dumps(get_project(conn, args.slug), ensure_ascii=False, indent=2))
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    return 0
