"""schema — le schéma SQLite unique du cockpit. **Contrat figé** (cf. docs/schema-contract.md) : une
migration change ce fichier + bump `SCHEMA_VERSION` + entrée CHANGELOG, jamais en douce.

Modèle cœur (décision feature-groupe-des-tasks) : un **projet** possède des **features** (= branche =
worktree, l'unité de merge) ; une feature possède des **tasks** ordonnancées par un DAG `depends_on`
(unités de dispatch séquentielles) ; un **dispatch_job** matérialise un run worker sur une task ; une
**port_reservation** épingle 1 port par worktree (broker déterministe, spec worktree-cleanup).

`depends_on` est stocké en JSON (liste d'ids de tasks) — le résolveur (roadmap/resolver, spec
task-next-resolver-dag) dérive le séquencement ; le schéma ne fait que porter la donnée.

Historique de version : v1 = projects/features/tasks/dispatch_jobs (4 tables). v2 = `dispatch_jobs`
gagne `session_id` (LA clé de suivi live — le chemin du transcript en dérive) + les métriques
`num_turns`/`cost_usd`/`wall_s`/`engine` ; nouvelle table `port_reservations`. v3 = `projects` gagne
`kind` (`project|tool` — classification, une seule table plutôt que deux) + `owner` (nullable, compat
multi-utilisateur : à qui appartient l'entité). v4 = `projects` gagne `credential_ref` (nullable) : une
**référence opaque** vers le token du store de secrets (jamais le secret en clair en DB — spec
merge-writeback), résolue à l'usage au writeback git. v5 = `projects` gagne `source_url` (nullable,
provenance d'un projet adopté). v6 (typed-bundles) = `projects` gagne `project_type` (enum du bundle semé
à la création : `generic|service-api|cli-tool|front-ts`, défaut `generic`) ; `features` gagne `facet`
(nullable : la facette de dispatch — `backend|frontend|tool|doc` — qui aligne le worker) ; `tasks` gagne
`acceptance` (nullable, TEXT libre : critères de DoD injectés dans le prompt worker). v7
(runtime-hosting) = nouvelle table `deployments` : un **projet** possède **2 déploiements par branche**
(`main`=prod, `dev`=preview — spec project-hosting-branch-deploy-model) ; état de run + port + url + sha
du dernier deploy. Substrat conteneur (compose) en lightweight ; le modèle d'identité est agnostique du
substrat. Table neuve → créée sur base existante par `CREATE IF NOT EXISTS` (pas d'`ensure_columns`). v8
(bundle-storage-registry) = le `CHECK` figé sur `projects.project_type` est RETIRÉ : l'enum devient
**registre-driven** (`provision.discover_types`), l'autorité passe à `provision.validate_bundle` dans
`create_project`. SQLite ne sait pas ALTER un CHECK → rebuild de table gardé (`_migrate_v8_...`). v9
(board-native blueprint) = `features` gagne `blueprint` (nullable) : la **ref STAMP** (id d'un blueprint du
capital central, ex. `deterministic-tooling-gate`) portée par une feature ; l'id est résolu **au read du
board** via le client MCP (`GET …/roadmap` → `{blueprint:{id, resolved, reason, …}}`, honnête si MCP coupé).
v10 (inter-feature deps) = `features` gagne `depends_on` (NOT NULL, défaut `'[]'`) : le **DAG INTER-feature**
(liste JSON de slugs de features prérequises), symétrique de `tasks.depends_on`. Une feature reste
non-dispatchable tant qu'une prérequise n'est pas `merged` (enforce par `orchestrator`, validé par `check` —
spec cockpit-roadmap-inter-feature-deps). v11 (trace durable des échecs) = `dispatch_jobs` gagne `kind`
(identité du run — enum COMPLET `task|review|toolchain|fix` posé dès maintenant, SQLite n'ALTER pas un CHECK)
et `error` (raison d'échec courte, nullable ; le `raw` reste sur `log_path`, jamais recopié) ; deux tables
neuves : `non_runs` (journal PUR des runs jamais lancés — un skip a une raison, pas de pid/port/session ;
jamais lu par une garde) et `gate_verdicts` (historique des verdicts PAR SHA — `review`/`toolchain`/`merge`,
là où `write_verdict` écrasait). Ce commit pose les CONTENANTS ; les puits qui écrivent (dé-squat du reviewer,
`record_finish` qui garde `error`, historisation au `write_verdict`/`run_merge`) sont câblés par les filles
`cockpit-trace-job-sinks` / `cockpit-gate-verdict-history`.
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 11

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
        kind          TEXT NOT NULL DEFAULT 'project'   -- classification (v3) : projet travaillé vs outil
                          CHECK (kind IN ('project', 'tool')),
        owner         TEXT,                            -- à qui appartient l'entité (v3, nullable : mono-user)
        credential_ref TEXT,                           -- réf opaque vers le token du store (v4, nullable)
        source_url    TEXT,                            -- URL adoptée (v5) : provenance (jamais un secret)
        project_type  TEXT NOT NULL DEFAULT 'generic',  -- bundle semé (v6) ; CHECK retiré en v8
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
        facet         TEXT,                          -- facette de dispatch (v6, nullable ; défaut bundle)
        blueprint     TEXT,                          -- ref blueprint STAMP (v9, nullable ; id résolu via MCP)
        depends_on    TEXT NOT NULL DEFAULT '[]',    -- JSON: slugs de features (DAG INTER-feature, v10)
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
        acceptance  TEXT,                            -- critères de DoD (v6, nullable) → prompt worker
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
        kind          TEXT NOT NULL DEFAULT 'task'   -- identité du run (v11) : ouvrier de task vs reviewer/…
                          CHECK (kind IN ('task', 'review', 'toolchain', 'fix')),
        log_path      TEXT,                          -- transcript JSONL local (dérivé de session_id)
        session_id    TEXT,                          -- session `claude -p` = LE handle de suivi live (v2)
        num_turns     INTEGER,                       -- métriques du run (v2)
        cost_usd      REAL,
        wall_s        REAL,
        engine        TEXT,
        error         TEXT,                          -- raison d'échec courte (v11 ; raw sur log_path)
        started_at    TEXT,
        ended_at      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS port_reservations (
        id         TEXT PRIMARY KEY,
        project    TEXT NOT NULL,
        purpose    TEXT NOT NULL,                    -- ex. 'worktree:<feature>'
        port       INTEGER NOT NULL UNIQUE,          -- mono-hôte WSL : unicité GLOBALE du port
        created_at TEXT NOT NULL,
        UNIQUE (project, purpose)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deployments (
        id              TEXT PRIMARY KEY,
        project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        branch          TEXT NOT NULL                 -- 2 déploiements par projet : prod + preview
                            CHECK (branch IN ('main', 'dev')),
        -- Enum de statut ENCODÉ EN PLEIN dès v7 alors que v7 n'écrit que 'no_deploy' : SQLite ne sait pas
        -- ALTER un CHECK (il faudrait un rebuild de table), donc on pose l'enum cible du runtime dès
        -- maintenant → P5 (observabilité) écrit running|stopped|unhealthy|building SANS toucher au schéma.
        status          TEXT NOT NULL DEFAULT 'no_deploy'
                            CHECK (status IN ('no_deploy', 'building', 'running', 'stopped', 'unhealthy')),
        port            INTEGER,                      -- port de service alloué (broker deploy, P2), nullable
        url             TEXT,                         -- endpoint servi (P2/P5), nullable tant que no_deploy
        compose_ref     TEXT,                         -- réf du compose-project (unité d'isolation, P2)
        last_deploy_sha TEXT,                         -- sha buildé au dernier deploy (P2)
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        UNIQUE (project_id, branch)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS non_runs (
        id          TEXT PRIMARY KEY,
        feature_ref TEXT NOT NULL,                 -- "projet/feature" — scope (journal pur, pas de FK)
        kind        TEXT NOT NULL                  -- ce qui AURAIT couru (enum aligné sur dispatch_jobs.kind)
                        CHECK (kind IN ('task', 'review', 'toolchain', 'fix')),
        reason      TEXT NOT NULL,                 -- raison de non-lancement verbatim (readiness/hold)
        created_at  TEXT NOT NULL                  -- ts INJECTÉ par l'appelant (jamais un _now() interne)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gate_verdicts (
        id          TEXT PRIMARY KEY,
        project     TEXT NOT NULL,
        feature     TEXT NOT NULL,
        gate        TEXT NOT NULL                  -- review (Tier-1) / toolchain (Tier-0) / merge (GO humain)
                        CHECK (gate IN ('review', 'toolchain', 'merge')),
        sha         TEXT NOT NULL,                 -- SHA de HEAD ancré (historisé PAR SHA, pas d'écrasement)
        verdict     TEXT NOT NULL,                 -- payload JSON du verdict (tel qu'écrit sur disque)
        created_at  TEXT NOT NULL
    )
    """,
)

# Colonnes ajoutées à une table pré-existante après sa v1 (chemin ALTER idempotent — cf. `ensure_columns`).
# Une base neuve reçoit déjà tout par `DDL` ; ceci ne sert qu'à faire évoluer une base v1 en place.
_ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    # v11 : `kind` (NOT NULL, défaut littéral 'task' — ALTER exige un défaut littéral ; le CHECK n'est PAS
    # re-portable par ALTER en SQLite → l'invariant `kind∈{task,review,toolchain,fix}` est tenu côté DDL (base
    # neuve) ET par le code qui écrit — jamais un kind hors-enum inséré). Les jobs pré-v11 sont tous des runs
    # d'ouvrier → 'task' est exact pour l'existant. `error` (nullable, aucun défaut → NULL pour l'existant).
    "dispatch_jobs": (
        ("session_id", "TEXT"), ("num_turns", "INTEGER"), ("cost_usd", "REAL"),
        ("wall_s", "REAL"), ("engine", "TEXT"),
        ("kind", "TEXT NOT NULL DEFAULT 'task'"), ("error", "TEXT"),
    ),
    # v3 : ALTER exige un défaut LITTÉRAL pour une colonne NOT NULL (d'où 'project'). La contrainte CHECK
    # n'est pas re-portable par ALTER en SQLite → l'invariant `kind∈{project,tool}` est tenu côté DDL (base
    # neuve) ET validé par `registry.create_project` (toute base) — jamais un kind hors-enum inséré.
    # v4 : `credential_ref` (nullable, aucun défaut → NULL pour l'existant : les projets pré-v4 n'ont pas de
    # token lié tant que l'onboarding ne pose pas de référence).
    # v5 : `source_url` (nullable) = provenance d'un projet ADOPTÉ (clone d'un repo distant) ; NULL pour un
    # projet semé. Métadonnée pure (jamais un secret), habilite la reprise idempotente + le refresh.
    # v6 : `project_type` (NOT NULL, défaut littéral 'generic' — ALTER exige un défaut littéral). En v8 le
    # CHECK figé a été retiré (rebuild de table, `_migrate_v8_drop_project_type_check`) → l'enum est désormais
    # registre-driven, tenu par `provision.validate_bundle`. `features.facet`/`tasks.acceptance` : nullables.
    # v9 : `features.blueprint` (nullable, aucun défaut → NULL pour l'existant) = ref STAMP résolue au read.
    "projects": (("kind", "TEXT NOT NULL DEFAULT 'project'"), ("owner", "TEXT"),
                 ("credential_ref", "TEXT"), ("source_url", "TEXT"),
                 ("project_type", "TEXT NOT NULL DEFAULT 'generic'")),
    # v10 : `features.depends_on` (NOT NULL, défaut littéral '[]' — ALTER exige un défaut littéral) = DAG
    # INTER-feature (liste JSON de slugs de features prérequises). Symétrique de `tasks.depends_on` ;
    # ALTER-safe (pas de CHECK). Invariant « prérequis satisfait = merged » tenu par resolver/orchestrator.
    "features": (("facet", "TEXT"), ("blueprint", "TEXT"), ("depends_on", "TEXT NOT NULL DEFAULT '[]'")),
    "tasks": (("acceptance", "TEXT"),),
}

# Index de service (accès par clé étrangère / statut — les chemins chauds du résolveur et du dispatch).
INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_features_project ON features(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_feature ON tasks(feature_id)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_task ON dispatch_jobs(task_id)",
    "CREATE INDEX IF NOT EXISTS ix_deployments_project ON deployments(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_non_runs_feature ON non_runs(feature_ref)",
    "CREATE INDEX IF NOT EXISTS ix_gate_verdicts_feature ON gate_verdicts(project, feature)",
)


def create_schema(conn: sqlite3.Connection) -> None:
    """Crée toutes les tables + index (idempotent via `IF NOT EXISTS`), applique les migrations de colonnes
    en place puis pose `SCHEMA_VERSION`. Correct pour une base neuve (tout par `DDL`) comme pour une base
    d'une version antérieure (les `CREATE IF NOT EXISTS` ajoutent les tables neuves, `ensure_columns` les
    colonnes neuves)."""
    for stmt in DDL:
        conn.execute(stmt)
    for stmt in INDEXES:
        conn.execute(stmt)
    ensure_columns(conn)
    _migrate_v8_drop_project_type_check(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Ajoute (idempotent) les colonnes apparues après la v1 d'une table déjà créée (`ALTER TABLE ADD
    COLUMN` seulement si absente). Sans effet sur une base neuve (les colonnes sont déjà dans `DDL`)."""
    for table, cols in _ADDED_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue                 # table absente (PRAGMA vide) → rien à migrer (le DDL la créera)
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _migrate_v8_drop_project_type_check(conn: sqlite3.Connection) -> None:
    """v7→v8 : retire le `CHECK` figé sur `projects.project_type`. L'enum devient **registre-driven**
    (`provision.discover_types`) et l'autorité passe à `provision.validate_bundle` dans `create_project`.
    SQLite ne sait pas ALTER un CHECK → **rebuild de table**. GARDÉ (no-op si le CHECK est déjà absent : une
    base montée par ALTER/`ensure_columns` ne l'a jamais eu) et IDEMPOTENT. Les FK enfants
    (`features`/`tasks`/`deployments` → `projects.id`) restent valides : l'id est préservé et les FK sont
    désactivées le temps du rebuild."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()
    if row is None or "CHECK (project_type IN" not in row[0]:
        return                              # base neuve v8 (DDL sans CHECK) ou déjà migrée → no-op
    fk_prev = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.commit()                           # clôt toute transaction (PRAGMA FK est no-op en transaction)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        "BEGIN;\n"
        "CREATE TABLE projects_new (\n"
        "    id            TEXT PRIMARY KEY,\n"
        "    slug          TEXT NOT NULL UNIQUE,\n"
        "    name          TEXT NOT NULL,\n"
        "    sot_path      TEXT NOT NULL,\n"
        "    mirror_remote TEXT,\n"
        "    backend       TEXT NOT NULL DEFAULT 'internal' CHECK (backend IN ('internal', 'github')),\n"
        "    kind          TEXT NOT NULL DEFAULT 'project' CHECK (kind IN ('project', 'tool')),\n"
        "    owner         TEXT,\n"
        "    credential_ref TEXT,\n"
        "    source_url    TEXT,\n"
        "    project_type  TEXT NOT NULL DEFAULT 'generic',\n"
        "    created_at    TEXT NOT NULL\n"
        ");\n"
        "INSERT INTO projects_new SELECT id, slug, name, sot_path, mirror_remote, backend, kind, owner,"
        " credential_ref, source_url, project_type, created_at FROM projects;\n"
        "DROP TABLE projects;\n"
        "ALTER TABLE projects_new RENAME TO projects;\n"
        "COMMIT;\n"
    )
    if fk_prev:
        conn.execute("PRAGMA foreign_keys=ON")


def schema_version(conn: sqlite3.Connection) -> int:
    """Version de schéma posée sur cette base (0 si jamais initialisée)."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0
