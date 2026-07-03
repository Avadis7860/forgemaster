"""registry — CRUD des projets (create/list/get) sur la table `projects`. Un projet = identité durable :
`slug`, `sot_path` (bare local co-localisé), `mirror_remote` (best-effort), `backend` (internal|github).

Port : `services/aggregator/routers/projects.py` (registre). Refactor #1 (reçoit `settings` + connexion,
zéro module-global) / #4 (SoT sous `settings.projects_root`, aucun chemin d'hôte en dur).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from cockpit.config import Settings
from cockpit.core import ids
from cockpit.db import store
from cockpit.git.internal import GitOpError, InternalGit, classify_push_error, credential_env
from cockpit.provision import BUNDLE_TYPES, load_bundle
from cockpit.secrets import cred_resolver

_COLS = ("id", "slug", "name", "sot_path", "mirror_remote", "backend", "kind", "owner",
         "credential_ref", "source_url", "project_type", "created_at")
_KINDS = ("project", "tool")   # classification (v3) : entité travaillée vs outil générique du framework


def _now() -> str:
    return datetime.now(UTC).isoformat()


def sot_path_for(settings: Settings, slug: str) -> Path:
    """Chemin du SoT bare d'un projet : `<projects_root>/<slug>/sot.git`. Déterministe, sous config."""
    return settings.projects_root / slug / "sot.git"


def _slug_exists(conn: sqlite3.Connection, slug: str) -> bool:
    return conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone() is not None


def create_project(conn: sqlite3.Connection, settings: Settings, *,
                   slug: str, name: str | None = None, mirror_remote: str | None = None,
                   kind: str = "project", owner: str | None = None,
                   credential_ref: str | None = None, source_url: str | None = None,
                   project_type: str = "generic",
                   cred_resolver: Callable[[str], str] | None = None) -> dict:
    """Crée une entité (row DB) et **initialise son SoT bare local**. Deux chemins :

    - **SEED** (défaut, `source_url=None`) : SoT semé du bundle `base ⊕ overlay(project_type)` (idempotent).
      L'INSERT précède le seed (compat historique). `project_type` (défaut `generic`) choisit l'overlay.
    - **ADOPTION** (`source_url` fourni) : le SoT est un **clone bare** du repo distant (son VRAI historique).
      Le clone est fait **AVANT l'INSERT** → un clone échoué ne laisse **pas** de row orpheline (reprise de
      bootstrap propre). Auth optionnelle : si `credential_ref` + `cred_resolver`, le token est résolu à
      l'usage et injecté transitoirement (`credential_env`) ; sinon clone anonyme (repo public). Un échec de
      clone remonte en `ValueError` (routé en 400/erreur CLI) avec un hint (`classify_push_error`).

    `kind` classe l'entité ; `owner`/`credential_ref` nullable ; `source_url` (nullable) = provenance
    persistée (métadonnée, pas un secret). Lève `ValueError` si slug/`kind` invalide, si le slug existe déjà,
    ou si le clone échoue."""
    ids.ensure_slug(slug, field="project")
    if kind not in _KINDS:
        raise ValueError(f"kind invalide : {kind!r} (attendu {' | '.join(_KINDS)})")
    if project_type not in BUNDLE_TYPES:   # re-valide l'enum (le CHECK DDL le tient côté DB base neuve)
        raise ValueError(f"type invalide : {project_type!r} (attendu {' | '.join(BUNDLE_TYPES)})")
    sot = sot_path_for(settings, slug)
    git = InternalGit()
    if source_url:
        if _slug_exists(conn, slug):        # pré-check → éviter un clone gâché avant le heurt d'unicité
            raise ValueError(f"projet déjà existant : {slug!r}")
        creds_env = None
        if credential_ref and cred_resolver is not None:
            token = cred_resolver(credential_ref)     # total : '' si absent/illisible → clone anonyme
            if token:
                creds_env = credential_env(token)
        try:
            git.clone_sot(sot, source_url, creds_env=creds_env)
        except GitOpError as exc:
            hint = classify_push_error("", str(exc))
            raise ValueError(
                f"clone échoué ({hint}) : {source_url} — introuvable ou token sans accès ({exc})") from exc
    row = {"id": ids.new_id(), "slug": slug, "name": name or slug, "sot_path": str(sot),
           "mirror_remote": mirror_remote, "backend": "internal", "kind": kind, "owner": owner,
           "credential_ref": credential_ref, "source_url": source_url, "project_type": project_type,
           "created_at": _now()}
    try:
        conn.execute(
            "INSERT INTO projects (id, slug, name, sot_path, mirror_remote, backend, kind, owner, "
            "credential_ref, source_url, project_type, created_at) VALUES (:id, :slug, :name, :sot_path, "
            ":mirror_remote, :backend, :kind, :owner, :credential_ref, :source_url, :project_type, "
            ":created_at)", row)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        if source_url:
            shutil.rmtree(sot, ignore_errors=True)    # course perdue → rollback du clone qu'on vient de faire
        raise ValueError(f"projet déjà existant : {slug!r}") from exc
    if not source_url:
        git.init_sot(sot, payload=load_bundle(project_type))   # SEED : bundle du type (base ⊕ overlay)
    return row


def set_credential_ref(conn: sqlite3.Connection, slug: str, credential_ref: str | None) -> dict:
    """Lie (ou délie, si `None`) un `credential_ref` à un projet — l'affordance « token par repo » de
    l'onboarding écrit ICI. La DB ne porte que la **référence** opaque ; la valeur du token vit dans le
    store de secrets. Lève `KeyError` si le projet n'existe pas. Retourne le projet relu."""
    cur = conn.execute("UPDATE projects SET credential_ref = ? WHERE slug = ?", (credential_ref, slug))
    if cur.rowcount == 0:
        raise KeyError(slug)
    conn.commit()
    return get_project(conn, slug)


def set_mirror_remote(conn: sqlite3.Connection, slug: str, mirror_remote: str | None) -> dict:
    """Configure (ou retire, si `None`/vide) le **miroir GitHub** d'un projet — l'affordance « rendre
    GitHub-backed » de l'onboarding écrit ICI. Un miroir configuré fait qu'un token de push devient
    *requis* (best-effort : le SoT local reste la vérité). Lève `KeyError` si le projet n'existe pas.
    Retourne le projet relu."""
    normalized = (mirror_remote or "").strip() or None
    cur = conn.execute("UPDATE projects SET mirror_remote = ? WHERE slug = ?", (normalized, slug))
    if cur.rowcount == 0:
        raise KeyError(slug)
    conn.commit()
    return get_project(conn, slug)


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
            p = create_project(conn, settings, slug=args.slug, name=args.name,
                               kind=getattr(args, "kind", "project"),
                               source_url=getattr(args, "source_url", None),
                               project_type=getattr(args, "project_type", "generic"),
                               cred_resolver=cred_resolver(settings))
            how = f"adopté ← {p['source_url']}" if p.get("source_url") else f"SoT {p['sot_path']}"
            typ = "" if p["project_type"] == "generic" else f" [{p['project_type']}]"
            print(f"{p['kind']}{typ} créé : {p['slug']} — {how}")
        elif args.action == "list":
            for p in list_projects(conn):
                print(f"{p['slug']}\t{p['kind']}\t{p['backend']}\t{p['name']}")
        elif args.action == "get":
            print(json.dumps(get_project(conn, args.slug), ensure_ascii=False, indent=2))
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    return 0
