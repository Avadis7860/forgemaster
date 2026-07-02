"""store — ouverture et migration de la base SQLite du cockpit. Connexion configurée une fois
(row_factory dict-like, FK ON, WAL pour la concurrence CLI↔daemon), migration idempotente.

Le CRUD haut-niveau (create_project/list_features/…) sera porté à la phase logique via `projects.registry`
et consorts, qui reçoivent une connexion — jamais un module-global (correctif anti god-module)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cockpit.config import Settings
from cockpit.db import schema


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Ouvre une connexion configurée : dossier parent créé, `Row` dict-like, FK activées, WAL."""
    fp = Path(db_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(fp))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Applique le schéma si la base est vierge (ou en retard). Idempotent. Retourne la version finale.
    V1 : création directe (pas encore de chemin de migration multi-version à gérer)."""
    if schema.schema_version(conn) < schema.SCHEMA_VERSION:
        schema.create_schema(conn)
    return schema.schema_version(conn)


def open_db(settings: Settings) -> sqlite3.Connection:
    """Connexion migrée à la base du cockpit (`settings.db_path`). Point d'entrée des couches."""
    conn = connect(settings.db_path)
    migrate(conn)
    return conn
