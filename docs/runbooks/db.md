# db — runbook (contrat SQLite figé : ouverture, migrations additives, versionnement de schéma)

Le schéma SQLite du forgemaster est un **contrat figé** : le fichier `schema.py` porte l'unique définition, et toute
évolution passe par un triptyque discipliné — modifier le DDL + bumper `SCHEMA_VERSION` + inscrire une entrée
CHANGELOG (docstring d'historique de version) — jamais en douce. Les migrations sont **additives** : tables
neuves via `CREATE IF NOT EXISTS`, colonnes neuves via `ALTER ADD COLUMN` idempotent. Rien de destructif silencieux
— l'unique rebuild de table (`_migrate_v8`) préserve les ids et les FK. Invariant central : `migrate()` applique le
schéma tant que la base est en retard sur `SCHEMA_VERSION`, puis la scelle avec `PRAGMA user_version`.

## connect() — ouvre une connexion configurée une fois
`src/forgemaster/db/store.py:16` · appelé par `open_db()`
Crée le dossier parent, pose `row_factory = Row` (accès dict-like), `PRAGMA foreign_keys = ON`, `journal_mode = WAL`
et `busy_timeout = 5000`. Le WAL + busy_timeout servent la concurrence CLI↔daemon sous `forgemaster run`
(orchestrateur parallèle : N lecteurs + 1 écrivain, 5 s de retry absorbent les rares chevauchements). Bénin pour
tout appelant mono.

## migrate() — applique le schéma si la base est en retard (idempotent)
`src/forgemaster/db/store.py:33` · appelé par `open_db()` · retourne la version finale
Compare `schema.schema_version(conn)` à `schema.SCHEMA_VERSION` ; si strictement inférieure, délègue tout à
`schema.create_schema()` (qui gère base neuve ET évolution en place). Idempotent : une base à jour ne déclenche
aucune écriture. Retourne la version finale posée.

## open_db() — point d'entrée : connexion migrée à la base du forgemaster
`src/forgemaster/db/store.py:42` · appelé par les couches supérieures (registry, CRUD)
Compose `connect(settings.db_path)` puis `migrate(conn)` et rend la connexion prête. Note d'archi : le CRUD
haut-niveau reçoit cette connexion en argument — jamais un module-global (correctif anti god-module).

## create_schema() — crée tables + index, migre les colonnes, scelle la version
`src/forgemaster/db/schema.py:350` · appelé par `migrate()`
Exécute tout le `DDL` (7 tables, `IF NOT EXISTS`) puis les `INDEXES`, appelle `ensure_columns()` pour le chemin
ALTER, joue les **migrations de table** dans l'ordre — `_migrate_v8_drop_project_type_check`,
`_migrate_v15_dispatch_status_rate_limited`, `_migrate_v16_dispatch_status_interrupted`,
`_migrate_v19_alerts_kind_review_findings` — et scelle avec `PRAGMA user_version = SCHEMA_VERSION` + commit.
Correct pour une base neuve (tout par DDL) comme pour une base d'une version antérieure. `SCHEMA_VERSION`
(`schema.py:104`) ; l'historique de version vit dans la docstring de module (le CHANGELOG du contrat) — c'est
lui qu'on lit, pas ce runbook, pour savoir où en est le contrat.

## ensure_columns() — chemin ALTER additif idempotent
`src/forgemaster/db/schema.py:368` · appelé par `create_schema()`
Pour chaque table de `_ADDED_COLUMNS`, lit `PRAGMA table_info` et `ALTER TABLE ADD COLUMN` uniquement les colonnes
absentes. Sans effet sur une base neuve (les colonnes sont déjà dans le DDL) ; table absente → skip (le DDL la
créera). **Invariant migration additive** : un `ALTER` SQLite exige un défaut *littéral* pour une colonne NOT NULL
(d'où `'project'`, `'generic'`, `'[]'`) et ne re-porte **pas** un `CHECK` — les enums non-DDL sont donc tenus côté
application (`registry.create_project`, `provision.validate_bundle`).

## schema_version() — version posée sur la base (0 si vierge)
`src/forgemaster/db/schema.py:575` · appelé par `migrate()`
Lit `PRAGMA user_version` ; retourne 0 si la base n'a jamais été initialisée. C'est le curseur qui rend `migrate()`
idempotent et strictement croissant.

## _migrate_v8_drop_project_type_check() — l'unique rebuild de table (retrait de CHECK)
`src/forgemaster/db/schema.py:380` · appelé par `create_schema()`
Cas particulier de la contrainte « SQLite ne sait pas ALTER un CHECK ». Pour retirer le `CHECK` figé sur
`projects.project_type` (enum devenu registre-driven en v8), rebuild `projects` : `foreign_keys=OFF`, crée
`projects_new` sans le CHECK, `INSERT … SELECT` (ids préservés), `DROP`/`RENAME`, restaure les FK. **No-op idempotent**
si le CHECK est déjà absent (base neuve v8 ou déjà migrée). Les FK enfants restent valides car l'id est préservé.

## Zones non détaillées
- **DDL / _ADDED_COLUMNS / INDEXES** (`schema.py:46,151,178`) : données déclaratives du schéma, pas des fonctions —
  lues directement dans le fichier ; l'historique de version y est documenté en docstring de module.
- **CRUD haut-niveau** (`projects.registry` et consorts) : hors de ce module store/schema ; reçoit une connexion,
  porte l'autorité applicative sur les enums non-CHECK.
