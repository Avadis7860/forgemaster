"""model — CRUD de la roadmap (features + tasks) sur la DB, et (dé)sérialisation du contrat
`.cockpit/roadmap.yaml` (schéma figé — cf. docs/schema-contract.md § roadmap.yaml).

Une **feature** = branche = worktree (l'unité de merge) ; une **task** = unité de dispatch séquentielle,
`depends_on` explicite (JSON). Référence de feature = `"<projet>/<feature>"` (sans ambiguïté inter-projets).

Port : concept du contrat data v2 des tasks vault. Refactor #9 (la donnée porte `depends_on` ; le
séquencement est dérivé par `resolver`, pas ici).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime

import yaml

from cockpit.config import Settings
from cockpit.core import ids
from cockpit.db import store
from cockpit.projects.registry import get_project

ROADMAP_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


FACETS = ("backend", "frontend", "tool", "doc")   # facettes de dispatch (v6) — enum en code (cf. priority)


def add_feature(conn: sqlite3.Connection, *, project_slug: str, slug: str, title: str | None = None,
                facet: str | None = None) -> dict:
    """Ajoute une feature à un projet (branche `feature/<slug>`, statut `planned`). `facet` (optionnel) =
    la facette de dispatch qui alignera le worker ; NULL → défaut résolu du `bundle.toml` au dispatch."""
    ids.ensure_slug(slug, field="feature")
    if facet is not None and facet not in FACETS:
        raise ValueError(f"facette hors vocab {FACETS} : {facet!r}")
    project = get_project(conn, project_slug)
    row = {"id": ids.new_id(), "project_id": project["id"], "slug": slug, "title": title or slug,
           "branch": f"feature/{slug}", "worktree_path": None, "status": "planned", "facet": facet,
           "created_at": _now()}
    try:
        conn.execute(
            "INSERT INTO features (id, project_id, slug, title, branch, worktree_path, status, facet, "
            "created_at) VALUES (:id, :project_id, :slug, :title, :branch, :worktree_path, :status, :facet, "
            ":created_at)", row)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"feature déjà existante : {project_slug}/{slug}") from exc
    return row


def resolve_feature(conn: sqlite3.Connection, ref: str) -> dict:
    """Résout une référence `"<projet>/<feature>"` → row feature. Lève `KeyError`/`ValueError` sinon."""
    if "/" not in ref:
        raise ValueError(f"référence feature attendue « projet/feature » : {ref!r}")
    project_slug, feature_slug = ref.split("/", 1)
    project = get_project(conn, project_slug)
    r = conn.execute("SELECT * FROM features WHERE project_id = ? AND slug = ?",
                     (project["id"], feature_slug)).fetchone()
    if r is None:
        raise KeyError(ref)
    return dict(r)


def add_task(conn: sqlite3.Connection, *, feature_ref: str, slug: str, title: str | None = None,
             depends_on: list[str] | None = None, priority: str = "P1",
             acceptance: str | None = None) -> dict:
    """Ajoute une task à une feature. `depends_on` = slugs de tasks prérequises (dans la même feature).
    `acceptance` (optionnel, TEXT libre) = critères de DoD injectés dans le prompt du worker au dispatch."""
    ids.ensure_slug(slug, field="task")
    if priority not in ("P0", "P1", "P2", "P3"):
        raise ValueError(f"priorité hors vocab P0-P3 : {priority!r}")
    feature = resolve_feature(conn, feature_ref)
    row = {"id": ids.new_id(), "feature_id": feature["id"], "slug": slug, "title": title or slug,
           "status": "todo", "depends_on": json.dumps(depends_on or []), "priority": priority,
           "acceptance": acceptance, "created_at": _now()}
    try:
        conn.execute(
            "INSERT INTO tasks (id, feature_id, slug, title, status, depends_on, priority, acceptance, "
            "created_at) VALUES (:id, :feature_id, :slug, :title, :status, :depends_on, :priority, "
            ":acceptance, :created_at)", row)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"task déjà existante : {feature_ref}/{slug}") from exc
    return row


def list_features(conn: sqlite3.Connection, project_slug: str) -> list[dict]:
    project = get_project(conn, project_slug)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM features WHERE project_id = ? ORDER BY slug", (project["id"],))]


def list_tasks(conn: sqlite3.Connection, feature_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM tasks WHERE feature_id = ? ORDER BY slug", (feature_id,))
    out = []
    for r in rows:
        d = dict(r)
        d["depends_on"] = json.loads(d["depends_on"])
        out.append(d)
    return out


def _feature_doc(f: dict) -> dict:
    """Un bloc feature du contrat roadmap.yaml. `facet`/`acceptance` (v6) émis SEULEMENT si présents
    (contrat rétro-compatible : une roadmap sans facette reste identique à la v1)."""
    doc: dict = {"slug": f["slug"], "title": f["title"]}
    if f.get("facet"):
        doc["facet"] = f["facet"]
    doc["tasks"] = []
    for t in f.get("tasks", []):
        task: dict = {"slug": t["slug"], "title": t["title"], "priority": t["priority"],
                      "depends_on": t["depends_on"]}
        if t.get("acceptance"):
            task["acceptance"] = t["acceptance"]
        doc["tasks"].append(task)
    return doc


def to_yaml(project_slug: str, features: list[dict]) -> str:
    """Sérialise une roadmap (features + leurs tasks) au contrat `.cockpit/roadmap.yaml`. PUR."""
    doc = {"version": ROADMAP_VERSION, "project": project_slug,
           "features": [_feature_doc(f) for f in features]}
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit roadmap <action>` (add-feature|show)."""
    conn = store.open_db(settings)
    try:
        if args.action == "add-feature":
            f = add_feature(conn, project_slug=args.project, slug=args.slug, title=args.title)
            print(f"feature créée : {args.project}/{f['slug']} — branche {f['branch']}")
        elif args.action == "show":
            features = list_features(conn, args.project)
            for f in features:
                f["tasks"] = list_tasks(conn, f["id"])
            print(to_yaml(args.project, features))
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    return 0
