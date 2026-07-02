"""schema — le schéma SQLite unique du cockpit. **Contrat figé** (cf. docs/schema-contract.md) : une
migration change ce fichier + bump `SCHEMA_VERSION` + entrée CHANGELOG, jamais en douce.

Modèle cœur (décision feature-groupe-des-tasks) : un **projet** possède des **features** (= branche =
worktree, l'unité de merge) ; une feature possède des **tasks** ordonnancées par un DAG `depends_on`
(unités de dispatch séquentielles) ; un **dispatch_job** matérialise un run worker sur une task.

`depends_on` est stocké en JSON (liste d'ids de tasks) — le résolveur (roadmap/resolver, spec
task-next-resolver-dag) dérive le séquencement ; le schéma ne fait que porter la donnée.
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

# Ordre = ordre de création (les FK pointent vers des tables déjà créées). Chaque table porte les
# invariants durs en contraintes SQL (NOT NULL, UNIQUE, FK, CHECK sur les enums de statut).
DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS projects (
        id            TEXT PRIMARY KEY,
        slug          TEXT NOT NULL UNIQUE,
        name          TEXT NOT NULL,
        sot_path      TEXT NOT NULL,                 -- repo SoT bare LOCAL co-localisé (spec forge-sot-local)
        mirror_remote TEXT,                          -- miroir GitHub best-effort (jamais bloquant), nullable
        backend       TEXT NOT NULL DEFAULT 'internal'
                          CHECK (backend IN ('internal', 'github')),
        created_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS features (
        id            TEXT PRIMARY KEY,
        project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        slug          TEXT NOT NULL,
        title         TEXT NOT NULL,
        branch        TEXT NOT NULL,                 -- feature/<slug>
        worktree_path TEXT,                          -- worktree attaché au SoT (mutex), nullable hors-vol
        status        TEXT NOT NULL DEFAULT 'planned'
                          CHECK (status IN ('planned', 'active', 'ready', 'merged', 'cancelled')),
        created_at    TEXT NOT NULL,
        UNIQUE (project_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id          TEXT PRIMARY KEY,
        feature_id  TEXT NOT NULL REFERENCES features(id) ON DELETE CASCADE,
        slug        TEXT NOT NULL,
        title       TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'todo'
                        CHECK (status IN ('todo', 'in_progress', 'done', 'blocked', 'cancelled')),
        depends_on  TEXT NOT NULL DEFAULT '[]',      -- JSON: liste d'ids de tasks (DAG intra-feature)
        priority    TEXT NOT NULL DEFAULT 'P1'
                        CHECK (priority IN ('P0', 'P1', 'P2', 'P3')),
        created_at  TEXT NOT NULL,
        UNIQUE (feature_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_jobs (
        id            TEXT PRIMARY KEY,
        task_id       TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        worktree_path TEXT NOT NULL,
        port          INTEGER,                       -- port couplé au worktree (spec worktree-cleanup)
        pid           INTEGER,
        status        TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'running', 'done', 'failed', 'killed')),
        log_path      TEXT,
        started_at    TEXT,
        ended_at      TEXT
    )
    """,
)

# Index de service (accès par clé étrangère / statut — les chemins chauds du résolveur et du dispatch).
INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_features_project ON features(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_feature ON tasks(feature_id)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_task ON dispatch_jobs(task_id)",
)


def create_schema(conn: sqlite3.Connection) -> None:
    """Crée toutes les tables + index (idempotent via `IF NOT EXISTS`) et pose `SCHEMA_VERSION`."""
    for stmt in DDL:
        conn.execute(stmt)
    for stmt in INDEXES:
        conn.execute(stmt)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def schema_version(conn: sqlite3.Connection) -> int:
    """Version de schéma posée sur cette base (0 si jamais initialisée)."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0
