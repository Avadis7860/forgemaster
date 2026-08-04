"""schema — le schéma SQLite unique du forgemaster. **Contrat figé** (cf. docs/schema-contract.md) : une
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
`cockpit-trace-job-sinks` / `cockpit-gate-verdict-history`. v12 (first-session-interview) = `tasks` gagne
`mode` (identité de dispatch d'une task — enum `headless|interactive`, défaut `headless`) : une task
`interactive` est routée vers un **terminal interactif** (`forgemaster interview`) au lieu d'un worker
`claude -p` headless (une interview / un cadrage ne se mène pas en headless — cf. task
cockpit-first-session-interview). Le hint est posé par la GRAINE (le socle semé marque ses tasks
`cadrage`/`interview`), lu par le dispatch générique — zéro heuristique métier dans le moteur. CHECK côté
DDL (base neuve) ; l'invariant
`mode∈{headless,interactive}` est tenu par le code qui écrit (SQLite n'ALTER pas un CHECK). Les tasks pré-v12
sont toutes des runs headless → `'headless'` est exact pour l'existant. v13 (token-cost-per-project) =
`dispatch_jobs` gagne l'**usage token** du run — `input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens` (INTEGER) et `model` (TEXT, modèle DOMINANT du run = clé `modelUsage` au `costUSD`
max) — extraits de l'event `result` du stream-json par `parse_headless_result`, persistés par `record_finish`.
Tous **nullables, aucun défaut** → ALTER-safe, NULL pour l'existant ET pour un run raté/killed (pas d'usage →
jamais un faux zéro). `cost_usd` (v2) porte déjà le `total_cost_usd` de Claude → on ne recalcule PAS le $ (pas
de table de prix locale qui périmerait). Sert l'agrégation coût step→feature→projet (`dispatch/cost.py`).
v14 (cost-includes-interview) = `projects` gagne `interview_session_ids` (TEXT, nullable) : liste JSON des
`session_id` des interviews de socle (`forgemaster interview`, un `claude` INTERACTIF). Une session
interactive
n'émet PAS d'event `result` (donc **pas de $** : `total_cost_usd`/`modelUsage` absents) mais son transcript
(`~/.claude/projects/…/<sid>.jsonl`) porte l'usage token par-tour → `dispatch/cost.py` le somme en une ligne
« interview / cadrage » **tokens-only** (le $ reste celui du drain). ALTER-safe ; NULL = aucune interview.
v15 (rate-limit-not-failure) = le `CHECK` de `dispatch_jobs.status` gagne `rate_limited` : un run rejeté par
le plafond 5 h de l'org (`rate_limit_event status:"rejected"`) est classé À PART — ce n'est pas un échec de la
task (l'orchestrateur TIENT la feature, re-dispatchable après reset), et un faux `failed` poisonnerait
l'instrumentation d'outcome. SQLite ne sait pas ALTER un CHECK → rebuild de table (`_migrate_v15_...`, patron
v8), gardé + idempotent. Classé par `parse_headless_result`/`record_finish` (cf. `dispatch/jobs.py`).
v16 (interrupt-not-failure) = le `CHECK` de `dispatch_jobs.status` gagne `interrupted` : un run coupé EN VOL
par un signal externe (SIGTERM/SIGINT — teardown d'environnement, OOM, fin de session ; marqueur `[Request
interrupted by user]`) est classé À PART — ce n'est pas un échec de la task (l'orchestrateur TIENT la feature,
re-dispatchable), et un faux `failed` (`claude -p rc=143`) poisonnerait l'instrumentation d'outcome comme un
rate-limit (v15) ou un succès relu killed (D3). SQLite ne sait pas ALTER un CHECK → rebuild de table
(`_migrate_v16_...`, patron v8/v15), gardé + idempotent. Classé par `parse_headless_result`/`record_finish`.
v17 (no-silent-block / alerts) = nouvelle table `alerts` : persiste TOUT blocage de drain actionnable (gate
rouge, hold socle, worker failed, rate_limited, interrupted, interview requise) → centre d'alertes poussé
(doctrine « aucun blocage silencieux »). Table neuve → créée sur base existante par `CREATE IF NOT EXISTS`
(précédent v11 `non_runs`/`gate_verdicts` ; pas de rebuild, pas d'`ensure_columns`). Dédup par index UNIQUE
PARTIEL `ux_alerts_open(project, feature_ref, kind) WHERE status='open'` : au plus UNE alerte ouverte / motif
(`emit_alert` UPSERT sur ce conflit, jamais un doublon). Lifecycle `open|acked|resolved` : `resolve_alerts`
ferme au succès (merge réussi / feature drainée / gate redevenue verte) pour que le compteur dise vrai. Émis
chokepoint de décision (orchestrateur/merge), best-effort (cf. `db/alerts.py`) — une alerte ratée ne casse
jamais un drain. Lu par le daemon (`GET /api/alerts`), poussé au header web (poll court).
v18 (gate-green-outcome / merge_outcomes) = nouvelle table `merge_outcomes` : rend la fiabilité du gate vert
MESURABLE. Chaque merge VERT y enregistre une ligne DURABLE et survivante (`record_merge`, best-effort au
chokepoint du merge) — le dénominateur « mergé vert au SHA X ». Son `outcome` (`held|reverted|refixed`) est
MUTABLE par MARQUE HUMAINE (`mark_outcome`) : le terrain n'a aucun signal automatique d'issue aval (pas de
concept revert ; refix ne touche pas une feature mergée ; merge_sha ≠ commit de merge), donc l'attribution est
humaine, avec une pré-suggestion advisory dérivée au read. Survit à `delete_branch`/`prune_verdicts` (table
dédiée, hors du journal borné `gate_verdicts`). Table neuve → `CREATE IF NOT EXISTS` (précédent v17 `alerts` ;
pas de rebuild, pas d'`ensure_columns`). Dédup par index UNIQUE `ux_merge_outcome(project, feature, sha)` (≤1
ligne / merge vert ; `record_merge` = `INSERT OR IGNORE`). Une agrégation (`reliability`, fold Python) expose
`(n_merges_verts, n_adverse, taux)` par projet ET global — lu par le daemon (`GET /api/reliability`).
v19 (honest-signal / review_findings) = le `CHECK` d'`alerts.kind` gagne `review_findings` : les findings
🟡/🟣 CONSULTATIFS d'une review Tier-1 (non-bloquants — ils ne tiennent JAMAIS le merge) deviennent une
alerte `severity='info'` pour REMONTER au centre d'alertes (« aucun signal faussement vert » : un défaut
jaune ne doit pas mourir dans la preview éphémère du gate). SQLite ne sait pas ALTER un CHECK → **rebuild**
(`_migrate_v19_...`, patron v8/v15/v16), gardé + idempotent. Résolue au changement de SHA / verdict redevenu
propre (`resolve_alerts`) ; hors `_WORKER_ALERT_KINDS` (portée par la review, pas le drain). Lockstep front :
`web/src/lib/schemas.ts` (zod `AlertSchema.kind`) + `NotificationCenter.tsx` (`KIND_LABEL`).
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 19

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
        interview_session_ids TEXT,                    -- JSON: session_id des interviews (v14, nullable)
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
        mode        TEXT NOT NULL DEFAULT 'headless'  -- dispatch (v12) : headless vs terminal interactif
                        CHECK (mode IN ('headless', 'interactive')),
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
                          CHECK (status IN ('pending', 'running', 'done', 'failed', 'killed',
                                            'rate_limited', 'interrupted')),
        kind          TEXT NOT NULL DEFAULT 'task'   -- identité du run (v11) : ouvrier de task vs reviewer/…
                          CHECK (kind IN ('task', 'review', 'toolchain', 'fix')),
        log_path      TEXT,                          -- transcript JSONL local (dérivé de session_id)
        session_id    TEXT,                          -- session `claude -p` = LE handle de suivi live (v2)
        num_turns     INTEGER,                       -- métriques du run (v2)
        cost_usd      REAL,                          -- total_cost_usd de Claude (v2) : $ jamais recalculé
        wall_s        REAL,
        engine        TEXT,
        input_tokens          INTEGER,               -- usage token du run (v13), extrait de result.usage
        output_tokens         INTEGER,
        cache_read_tokens     INTEGER,
        cache_creation_tokens INTEGER,
        model         TEXT,                           -- modèle dominant (v13 : clé modelUsage au costUSD max)
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
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id          TEXT PRIMARY KEY,
        project     TEXT NOT NULL,                 -- slug projet (scope + deep-link)
        feature_ref TEXT NOT NULL,                 -- "projet/feature" — scope de dedup/resolve (journal pur)
        feature     TEXT NOT NULL,                 -- slug feature (deep-link ?feature=<slug>)
        kind        TEXT NOT NULL                  -- enum : 6 blocages + 1 consultatif
                        CHECK (kind IN ('gate_red', 'worker_failed', 'rate_limited',
                                        'interrupted', 'socle_hold', 'interview_hold',
                                        'review_findings')),
        tier        TEXT                           -- contexte gate ; NULL hors-gate
                        CHECK (tier IS NULL OR tier IN ('tier0', 'tier1', 'tier1.5', 'native')),
        severity    TEXT NOT NULL DEFAULT 'blocker'
                        CHECK (severity IN ('blocker', 'warn', 'info')),
        reason      TEXT NOT NULL,                 -- motif court, 1 ligne lisible
        findings    TEXT,                          -- JSON optionnel : blockers[]/findings[] structurés
        status      TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'acked', 'resolved')),
        created_at  TEXT NOT NULL,                 -- 1re détection (ts INJECTÉ par l'appelant, jamais _now())
        updated_at  TEXT NOT NULL,                 -- dernière ré-détection (refresh d'upsert)
        resolved_at TEXT                           -- NULL tant que open/acked
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS merge_outcomes (
        id          TEXT PRIMARY KEY,
        project     TEXT NOT NULL,                 -- slug projet (scope agrégation + deep-link)
        feature     TEXT NOT NULL,                 -- slug feature
        feature_ref TEXT NOT NULL,                 -- "projet/feature" (journal pur dénormalisé, pas de FK)
        sha         TEXT NOT NULL,                 -- reviewed_sha du merge vert (ancre, dédup)
        human_go    INTEGER NOT NULL DEFAULT 1,    -- merge vert sous GO humain (0 = auto-merge futur)
        outcome     TEXT NOT NULL DEFAULT 'held'   -- issue AVAL observée (marque humaine ; 'held' = a tenu)
                        CHECK (outcome IN ('held', 'reverted', 'refixed')),
        note        TEXT,                          -- raison libre de la marque adverse (opt)
        merged_at   TEXT NOT NULL,                 -- instant du merge vert (dénominateur + partition signal)
        updated_at  TEXT NOT NULL,                 -- dernière écriture (record/mark)
        marked_at   TEXT                           -- instant de la marque adverse ; NULL tant que 'held'
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
    # v13 : usage token du run + modèle dominant, tous nullables SANS défaut → ALTER-safe (NULL pour
    # l'existant : les jobs pré-v13 n'ont pas d'usage extrait ; un run raté/killed non plus). Le $ reste
    # `cost_usd` (v2, = total_cost_usd de Claude) — pas de recalcul, pas de table de prix locale.
    "dispatch_jobs": (
        ("session_id", "TEXT"), ("num_turns", "INTEGER"), ("cost_usd", "REAL"),
        ("wall_s", "REAL"), ("engine", "TEXT"),
        ("kind", "TEXT NOT NULL DEFAULT 'task'"), ("error", "TEXT"),
        ("input_tokens", "INTEGER"), ("output_tokens", "INTEGER"),
        ("cache_read_tokens", "INTEGER"), ("cache_creation_tokens", "INTEGER"), ("model", "TEXT"),
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
    # v14 : `interview_session_ids` (nullable, NULL pour l'existant) = liste JSON de session_id d'interviews
    # (transcript interactif → usage token tokens-only, cf. dispatch/cost.py).
    "projects": (("kind", "TEXT NOT NULL DEFAULT 'project'"), ("owner", "TEXT"),
                 ("credential_ref", "TEXT"), ("source_url", "TEXT"),
                 ("project_type", "TEXT NOT NULL DEFAULT 'generic'"),
                 ("interview_session_ids", "TEXT")),
    # v10 : `features.depends_on` (NOT NULL, défaut littéral '[]' — ALTER exige un défaut littéral) = DAG
    # INTER-feature (liste JSON de slugs de features prérequises). Symétrique de `tasks.depends_on` ;
    # ALTER-safe (pas de CHECK). Invariant « prérequis satisfait = merged » tenu par resolver/orchestrator.
    "features": (("facet", "TEXT"), ("blueprint", "TEXT"), ("depends_on", "TEXT NOT NULL DEFAULT '[]'")),
    # v12 : `tasks.mode` (NOT NULL, défaut littéral 'headless' — ALTER exige un défaut littéral). Le CHECK
    # `mode∈{headless,interactive}` n'est PAS re-portable par ALTER en SQLite → tenu côté DDL (base neuve) ET
    # par le code qui écrit (`model.add_task`). Les tasks pré-v12 sont toutes des runs headless → exact.
    "tasks": (("acceptance", "TEXT"), ("mode", "TEXT NOT NULL DEFAULT 'headless'")),
}

# Index de service (accès par clé étrangère / statut — les chemins chauds du résolveur et du dispatch).
INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_features_project ON features(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_feature ON tasks(feature_id)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_task ON dispatch_jobs(task_id)",
    "CREATE INDEX IF NOT EXISTS ix_deployments_project ON deployments(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_non_runs_feature ON non_runs(feature_ref)",
    "CREATE INDEX IF NOT EXISTS ix_gate_verdicts_feature ON gate_verdicts(project, feature)",
    # Dédup : au plus UNE alerte OUVERTE par (projet, feature, motif) — cible de l'upsert d'`emit_alert`
    # (partiel `WHERE status='open'` → acked/resolved sortent de l'index ; une re-blocage ré-ouvre net).
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_alerts_open ON alerts(project, feature_ref, kind) "
    "WHERE status = 'open'",
    "CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts(status)",
    # Dédup : au plus UNE ligne d'issue par merge vert (project, feature, sha) — cible de l'`INSERT OR IGNORE`
    # de `record_merge` (un merge rejoué au même SHA ne double pas le dénominateur).
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_merge_outcome ON merge_outcomes(project, feature, sha)",
    "CREATE INDEX IF NOT EXISTS ix_merge_outcomes_project ON merge_outcomes(project)",
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
    _migrate_v15_dispatch_status_rate_limited(conn)
    _migrate_v16_dispatch_status_interrupted(conn)
    _migrate_v19_alerts_kind_review_findings(conn)
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


def _migrate_v15_dispatch_status_rate_limited(conn: sqlite3.Connection) -> None:
    """v14→v15 : le `CHECK` de `dispatch_jobs.status` gagne `rate_limited` (un rejet rate-limit 5 h de l'org
    n'est PAS un échec de task — cf. `record_finish`/orchestrator). SQLite ne sait pas ALTER un CHECK →
    **rebuild de table** (même patron que `_migrate_v8_...`). GARDÉ (no-op si le CHECK contient déjà
    `rate_limited` — base neuve v15 par DDL, ou déjà migrée) et IDEMPOTENT. Rien ne référence `dispatch_jobs`
    en FK enfant ; sa propre FK (`task_id → tasks.id`) est préservée (id conservé, FK OFF au rebuild)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='dispatch_jobs'").fetchone()
    if row is None or "rate_limited" in row[0]:
        return                              # base neuve v15 (CHECK à jour) ou déjà migrée → no-op
    fk_prev = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.commit()                           # clôt toute transaction (PRAGMA FK est no-op en transaction)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        "BEGIN;\n"
        "CREATE TABLE dispatch_jobs_new (\n"
        "    id            TEXT PRIMARY KEY,\n"
        "    task_id       TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,\n"
        "    worktree_path TEXT NOT NULL,\n"
        "    port          INTEGER,\n"
        "    pid           INTEGER,\n"
        "    status        TEXT NOT NULL DEFAULT 'pending'\n"
        "                      CHECK (status IN ('pending', 'running', 'done', 'failed', 'killed',\n"
        "                                        'rate_limited')),\n"
        "    kind          TEXT NOT NULL DEFAULT 'task'\n"
        "                      CHECK (kind IN ('task', 'review', 'toolchain', 'fix')),\n"
        "    log_path      TEXT,\n"
        "    session_id    TEXT,\n"
        "    num_turns     INTEGER,\n"
        "    cost_usd      REAL,\n"
        "    wall_s        REAL,\n"
        "    engine        TEXT,\n"
        "    input_tokens          INTEGER,\n"
        "    output_tokens         INTEGER,\n"
        "    cache_read_tokens     INTEGER,\n"
        "    cache_creation_tokens INTEGER,\n"
        "    model         TEXT,\n"
        "    error         TEXT,\n"
        "    started_at    TEXT,\n"
        "    ended_at      TEXT\n"
        ");\n"
        "INSERT INTO dispatch_jobs_new SELECT id, task_id, worktree_path, port, pid, status, kind, log_path,"
        " session_id, num_turns, cost_usd, wall_s, engine, input_tokens, output_tokens, cache_read_tokens,"
        " cache_creation_tokens, model, error, started_at, ended_at FROM dispatch_jobs;\n"
        "DROP TABLE dispatch_jobs;\n"
        "ALTER TABLE dispatch_jobs_new RENAME TO dispatch_jobs;\n"
        "COMMIT;\n"
    )
    if fk_prev:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_v16_dispatch_status_interrupted(conn: sqlite3.Connection) -> None:
    """v15→v16 : le `CHECK` de `dispatch_jobs.status` gagne `interrupted` (un SIGTERM externe qui coupe le
    worker en vol n'est PAS un échec de task — cf. `record_finish`/orchestrator). SQLite ne sait pas ALTER un
    CHECK → **rebuild de table** (même patron que `_migrate_v15_...`/`_migrate_v8_...`). GARDÉ (no-op si le
    CHECK contient déjà `interrupted` — base neuve v16 par DDL, ou déjà migrée) et IDEMPOTENT. Le CHECK
    reconstruit porte les DEUX ajouts (`rate_limited` de v15 + `interrupted`) : un v14→v16 enchaîne les deux
    rebuilds, un v15→v16 n'ajoute que `interrupted`. FK propre préservée (id conservé, FK OFF au rebuild)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='dispatch_jobs'").fetchone()
    if row is None or "interrupted" in row[0]:
        return                              # base neuve v16 (CHECK à jour) ou déjà migrée → no-op
    fk_prev = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.commit()                           # clôt toute transaction (PRAGMA FK est no-op en transaction)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        "BEGIN;\n"
        "CREATE TABLE dispatch_jobs_new (\n"
        "    id            TEXT PRIMARY KEY,\n"
        "    task_id       TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,\n"
        "    worktree_path TEXT NOT NULL,\n"
        "    port          INTEGER,\n"
        "    pid           INTEGER,\n"
        "    status        TEXT NOT NULL DEFAULT 'pending'\n"
        "                      CHECK (status IN ('pending', 'running', 'done', 'failed', 'killed',\n"
        "                                        'rate_limited', 'interrupted')),\n"
        "    kind          TEXT NOT NULL DEFAULT 'task'\n"
        "                      CHECK (kind IN ('task', 'review', 'toolchain', 'fix')),\n"
        "    log_path      TEXT,\n"
        "    session_id    TEXT,\n"
        "    num_turns     INTEGER,\n"
        "    cost_usd      REAL,\n"
        "    wall_s        REAL,\n"
        "    engine        TEXT,\n"
        "    input_tokens          INTEGER,\n"
        "    output_tokens         INTEGER,\n"
        "    cache_read_tokens     INTEGER,\n"
        "    cache_creation_tokens INTEGER,\n"
        "    model         TEXT,\n"
        "    error         TEXT,\n"
        "    started_at    TEXT,\n"
        "    ended_at      TEXT\n"
        ");\n"
        "INSERT INTO dispatch_jobs_new SELECT id, task_id, worktree_path, port, pid, status, kind, log_path,"
        " session_id, num_turns, cost_usd, wall_s, engine, input_tokens, output_tokens, cache_read_tokens,"
        " cache_creation_tokens, model, error, started_at, ended_at FROM dispatch_jobs;\n"
        "DROP TABLE dispatch_jobs;\n"
        "ALTER TABLE dispatch_jobs_new RENAME TO dispatch_jobs;\n"
        "COMMIT;\n"
    )
    if fk_prev:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_v19_alerts_kind_review_findings(conn: sqlite3.Connection) -> None:
    """v18→v19 : le `CHECK` d'`alerts.kind` gagne `review_findings` (findings 🟡/🟣 CONSULTATIFS d'une review
    Tier-1 remontés au centre d'alertes en `severity='info'` — cf. `db/alerts`/`gate/review`). SQLite ne sait
    pas ALTER un CHECK → **rebuild** (patron `_migrate_v15_...`/`_migrate_v16_...`). GARDÉ (no-op si le CHECK
    contient déjà `review_findings` — base neuve v19 par DDL, ou déjà migrée) et IDEMPOTENT.

    DIVERGENCE (correcte) du patron : `alerts` porte un index UNIQUE PARTIEL `ux_alerts_open`, CIBLE de l'`ON
    CONFLICT` d'`emit_alert` (correctness, pas perf). Le rebuild DROP la table → perd ses index, et
    `create_schema` a passé son bloc INDEXES AVANT les migrations → sans recréation ici, l'UPSERT d'alerte
    casserait sur la base migrée (index absent). On RECRÉE donc les deux index dans l'`executescript`
    (contrairement à v15/v16 dont `ix_jobs_task` n'est que perf). `alerts` n'a aucune FK enfant ni parent."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'").fetchone()
    if row is None or "review_findings" in row[0]:
        return                              # base neuve v19 (CHECK à jour) ou déjà migrée → no-op
    conn.commit()                           # clôt toute transaction avant l'executescript
    conn.executescript(
        "BEGIN;\n"
        "CREATE TABLE alerts_new (\n"
        "    id          TEXT PRIMARY KEY,\n"
        "    project     TEXT NOT NULL,\n"
        "    feature_ref TEXT NOT NULL,\n"
        "    feature     TEXT NOT NULL,\n"
        "    kind        TEXT NOT NULL\n"
        "                    CHECK (kind IN ('gate_red', 'worker_failed', 'rate_limited',\n"
        "                                    'interrupted', 'socle_hold', 'interview_hold',\n"
        "                                    'review_findings')),\n"
        "    tier        TEXT\n"
        "                    CHECK (tier IS NULL OR tier IN ('tier0', 'tier1', 'tier1.5', 'native')),\n"
        "    severity    TEXT NOT NULL DEFAULT 'blocker'\n"
        "                    CHECK (severity IN ('blocker', 'warn', 'info')),\n"
        "    reason      TEXT NOT NULL,\n"
        "    findings    TEXT,\n"
        "    status      TEXT NOT NULL DEFAULT 'open'\n"
        "                    CHECK (status IN ('open', 'acked', 'resolved')),\n"
        "    created_at  TEXT NOT NULL,\n"
        "    updated_at  TEXT NOT NULL,\n"
        "    resolved_at TEXT\n"
        ");\n"
        "INSERT INTO alerts_new SELECT id, project, feature_ref, feature, kind, tier, severity, reason,"
        " findings, status, created_at, updated_at, resolved_at FROM alerts;\n"
        "DROP TABLE alerts;\n"
        "ALTER TABLE alerts_new RENAME TO alerts;\n"
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_alerts_open ON alerts(project, feature_ref, kind)"
        " WHERE status = 'open';\n"
        "CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts(status);\n"
        "COMMIT;\n"
    )


def schema_version(conn: sqlite3.Connection) -> int:
    """Version de schéma posée sur cette base (0 si jamais initialisée)."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0
