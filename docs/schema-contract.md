# schema-contract — contrats figés du cockpit

Trois schémas sont un **contrat** : une couche produit, une autre consomme. On change une *implémentation*
librement ; changer un **schéma** exige une entrée CHANGELOG + un bump. Un schéma partiel qui se dit complet
est un bug (jamais de cap silencieux).

## 1. Schéma SQLite (`db/schema.py`, `SCHEMA_VERSION` = **2**)

Base unique sous `settings.db_path` (`$COCKPIT_HOME/cockpit.db`). Modèle **feature-groupe-des-tasks**.

- **`projects`** — `id` (uuid), `slug` (kebab, unique), `name`, `sot_path` (repo bare LOCAL co-localisé),
  `mirror_remote` (miroir GitHub best-effort, nullable), `backend` (`internal`|`github`), `created_at`.
- **`features`** — `id`, `project_id`→projects (cascade), `slug`, `title`, `branch` (`feature/<slug>`),
  `worktree_path` (nullable hors-vol), `status` (`planned`|`active`|`ready`|`merged`|`cancelled`),
  `created_at` ; unique `(project_id, slug)`.
- **`tasks`** — `id`, `feature_id`→features (cascade), `slug`, `title`, `status`
  (`todo`|`in_progress`|`done`|`blocked`|`cancelled`), `depends_on` (**JSON** : liste d'ids de tasks, DAG
  intra-feature), `priority` (`P0`..`P3`), `created_at` ; unique `(feature_id, slug)`.
- **`dispatch_jobs`** — `id`, `task_id`→tasks (cascade), `worktree_path`, `port` (couplé au worktree,
  nullable), `pid`, `status` (`pending`|`running`|`done`|`failed`|`killed`), `log_path` (transcript JSONL
  local dérivé de `session_id`), `session_id` (session `claude -p` = **handle de suivi live**, v2),
  `num_turns`/`cost_usd`/`wall_s`/`engine` (métriques du run, v2), `started_at`, `ended_at`.
- **`port_reservations`** (v2) — `id`, `project`, `purpose` (ex. `worktree:<feature>`), `port` (**unique
  global** : mono-hôte WSL), `created_at` ; unique `(project, purpose)`. Broker de ports déterministe :
  1 port stable/épinglé par worktree, relâché au teardown (spec worktree-cleanup).

Invariants durs portés par le SQL : FK + `ON DELETE CASCADE`, `CHECK` sur chaque enum de statut, `UNIQUE`
sur les slugs scopés. `PRAGMA foreign_keys=ON`, `journal_mode=WAL` (concurrence CLI↔daemon).

**Migration v1→v2** : `dispatch_jobs` gagne `session_id`+métriques (via `ensure_columns`, `ALTER ADD COLUMN`
idempotent) ; nouvelle table `port_reservations`. Une base neuve reçoit tout par `DDL` ; une base v1
existante est mise à niveau en place par `create_schema`.

## 2. Schéma `.cockpit/roadmap.yaml` (in-repo, `roadmap/model.py`)

Versionné **avec le projet** (source de vérité côté repo), synchronisé vers la DB (index). Manifeste SEC
(noms/listes, pas de prose libre). Forme :

```yaml
version: 1
features:
  - slug: <kebab>
    title: <str>
    phases: [[<task-slug>, …], [<task-slug>, …]]   # étapes ORDONNÉES ; chaque étape = ids parallèles
    tasks:
      - slug: <kebab>
        title: <str>
        priority: P0|P1|P2|P3
        depends_on: [<task-slug>, …]                # intra-feature explicite (union avec phases:)
```

`phases:` (inter/intra ordonnancement) augmente `depends_on` **en union, jamais écrasement** ; sans
`phases:`, comportement identique au `depends_on` seul (opt-in rétro-compatible). Cf. spec
`task-next-resolver-dag`.

## 3. Contrat API HTTP (`daemon/`, à figer à la phase logique)

Le daemon expose le cœur ; le web (P5) le consomme. Routers par domaine (correctif #3) :
`/api/projects`, `/api/roadmap`, `/api/dispatch`, `/api/gate`, `/ws/terminal/{project}`,
`/ws/dispatch/{job}`. Le contrat détaillé (payloads, codes) sera figé ici quand la couche daemon est
portée — un endpoint qui borne/tronque le **signale** dans sa réponse.

## Politique de versionnage

- **Non-breaking** (ajout de colonne nullable, nouvelle route, nouveau champ optionnel) → pas de bump, note
  CHANGELOG.
- **Breaking** (renommage/suppression de colonne, changement d'enum, changement de sémantique d'un champ) →
  bump `SCHEMA_VERSION` (SQLite) / `version` (roadmap.yaml) + entrée CHANGELOG + chemin de migration.
