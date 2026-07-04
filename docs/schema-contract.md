# schema-contract — contrats figés du cockpit

Trois schémas sont un **contrat** : une couche produit, une autre consomme. On change une *implémentation*
librement ; changer un **schéma** exige une entrée CHANGELOG + un bump. Un schéma partiel qui se dit complet
est un bug (jamais de cap silencieux).

## 1. Schéma SQLite (`db/schema.py`, `SCHEMA_VERSION` = **6**)

Base unique sous `settings.db_path` (`$COCKPIT_HOME/cockpit.db`). Modèle **feature-groupe-des-tasks**.

- **`projects`** — `id` (uuid), `slug` (kebab, unique), `name`, `sot_path` (repo bare LOCAL co-localisé),
  `mirror_remote` (miroir GitHub best-effort, nullable), `backend` (`internal`|`github`), `kind`
  (`project`|`tool`, v3 — classification : entité travaillée vs outil générique du framework ; une seule
  table plutôt que deux), `owner` (nullable, v3 — compat multi-utilisateur), `credential_ref` (nullable, v4
  — **référence opaque** vers le token du store de secrets ; jamais le secret en clair en DB, résolu à
  l'usage au writeback git — spec merge-writeback), `source_url` (nullable, **v5** — provenance d'un projet
  **adopté** : l'URL clonée comme SoT ; `NULL` pour un projet semé ; **métadonnée, jamais un secret**),
  `project_type` (`generic`|`service-api`|`cli-tool`|`front-ts`, défaut `generic`, **v6** — le bundle semé à
  la création : quel overlay `base ⊕ types/<type>` a câblé le repo ; spec typed-bundles), `created_at`.
- **`features`** — `id`, `project_id`→projects (cascade), `slug`, `title`, `branch` (`feature/<slug>`),
  `worktree_path` (nullable hors-vol), `status` (`planned`|`active`|`ready`|`merged`|`cancelled`),
  `facet` (nullable, **v6** — la facette de dispatch `backend`|`frontend`|`tool`|`doc` qui aligne le worker ;
  `NULL` → défaut résolu du `.cockpit/bundle.toml` au dispatch), `created_at` ; unique `(project_id, slug)`.
- **`tasks`** — `id`, `feature_id`→features (cascade), `slug`, `title`, `status`
  (`todo`|`in_progress`|`done`|`blocked`|`cancelled`), `depends_on` (**JSON** : liste d'ids de tasks, DAG
  intra-feature), `priority` (`P0`..`P3`), `acceptance` (nullable TEXT, **v6** — critères de DoD injectés
  dans le prompt du worker au dispatch), `created_at` ; unique `(feature_id, slug)`.
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

**Migration v2→v3** : `projects` gagne `kind` (`TEXT NOT NULL DEFAULT 'project'`) + `owner` (`TEXT`,
nullable), via `ensure_columns` (`ALTER ADD COLUMN` idempotent — les lignes existantes prennent le défaut
littéral `kind='project'`). La contrainte `CHECK (kind IN ('project','tool'))` vit dans le `DDL` (base neuve)
et l'invariant est **re-validé** par `registry.create_project` (toute base) — un `ALTER` SQLite ne re-porte
pas le CHECK, mais aucun `kind` hors-enum ne peut être inséré. Ajout **non-breaking** (colonnes à défaut).

**Migration v3→v4** : `projects` gagne `credential_ref` (`TEXT`, nullable, **aucun défaut**) via
`ensure_columns` — les lignes existantes prennent `NULL` (aucun token lié rétroactivement ; l'onboarding
posera la référence via `registry.set_credential_ref`). La DB ne porte que la **référence** opaque ; la
valeur du token vit dans le store de secrets (`COCKPIT_SECRET_STORE`), résolue à l'usage au writeback git
(`gate/merge` → `git/internal.merge_writeback`, env `GIT_CONFIG_*` injecté le temps du push, jamais
persisté — spec merge-writeback). Ajout **non-breaking**.

**Migration v4→v5** : `projects` gagne `source_url` (`TEXT`, nullable, **aucun défaut**) via `ensure_columns`
— les lignes existantes (projets semés) prennent `NULL`. Habilite l'**adoption** : un projet créé avec
`source_url` a son SoT **cloné** du repo distant (son vrai historique) au lieu d'être semé du toolkit, et la
provenance est conservée (reprise idempotente / refresh). C'est une **métadonnée** (jamais un secret : l'auth
du clone privé reste dans le store via `credential_ref`). Ajout **non-breaking**.

**Migration v5→v6** (typed-bundles) : `projects` gagne `project_type` (`TEXT NOT NULL DEFAULT 'generic'` —
défaut littéral requis par `ALTER`), `features` gagne `facet` (`TEXT`, nullable), `tasks` gagne `acceptance`
(`TEXT`, nullable), via `ensure_columns`. Les lignes existantes prennent `project_type='generic'`, facet/
acceptance `NULL`. Le `CHECK` de `project_type` vit dans le `DDL` (base neuve) et l'invariant est **re-validé**
par `registry.create_project` ; l'enum `facet` est tenu en code par `roadmap/model.FACETS` (comme `priority`).
Ajout **non-breaking**.

## 2. Schéma `.cockpit/roadmap.yaml` (in-repo, `roadmap/model.py`)

Versionné **avec le projet** (source de vérité côté repo), synchronisé vers la DB (index). Manifeste SEC
(noms/listes, pas de prose libre). Forme :

```yaml
version: 1
features:
  - slug: <kebab>
    title: <str>
    facet: backend|frontend|tool|doc               # v6, OPTIONNEL — facette de dispatch (omis si non posée)
    phases: [[<task-slug>, …], [<task-slug>, …]]   # étapes ORDONNÉES ; chaque étape = ids parallèles
    tasks:
      - slug: <kebab>
        title: <str>
        priority: P0|P1|P2|P3
        depends_on: [<task-slug>, …]                # intra-feature explicite (union avec phases:)
        acceptance: <str>                           # v6, OPTIONNEL — critères de DoD injectés au prompt worker
```

`facet:`/`acceptance:` (v6) sont **émis seulement si présents** — une roadmap sans facette reste identique
au contrat v1 (rétro-compatible). `facet` tague la feature du type de travail (aligne le worker au dispatch) ;
`acceptance` porte les critères de succès de la task, rendus verbatim dans le prompt.

`phases:` (inter/intra ordonnancement) augmente `depends_on` **en union, jamais écrasement** ; sans
`phases:`, comportement identique au `depends_on` seul (opt-in rétro-compatible). Cf. spec
`task-next-resolver-dag`.

## 2b. Manifeste `bootstrap.yaml` (sous `COCKPIT_HOME`, `bootstrap.py`)

Édition **maintainer** : les outils du framework adoptés au 1er démarrage (`cockpit bootstrap` / wizard).
Manifeste SEC (noms/listes/URLs, **aucun secret**). Absent → no-op propre (install générique). Forme :

```yaml
tools:
  - slug: <kebab>                 # requis
    source_url: <url-git>         # requis : cloné comme SoT (P1) → VRAI contenu
    kind: tool                    # défaut 'tool' (rail section « Outils »)
    credential_ref: <réf-store>   # optionnel : réf OPAQUE (repo privé) ; absente = clone anonyme (public)
```

`credential_ref` (repo privé) = **un token de lecture par repo** ; sinon le `shared_ref` du wizard/
`--token-file` sert de repli ; sinon anonyme (repos publics, forward-compatible). Le token brut ne vit
JAMAIS ici — seulement dans le coffre. Amorçage **idempotent** (slug présent → `skipped`) ; échec d'une
entrée isolé (`failed`), un manifeste invalide avorte (fail-loud).

## 3. Contrat API HTTP (`daemon/`, porté)

Le daemon expose le cœur ; le web (P5) le consomme. **DI explicite** (`Deps` sur `app.state`, lu par
`get_deps` — aucun god-module) ; routers **fins** par domaine (correctif #3) ; erreurs domaine mappées
globalement (`KeyError`→404, `ValueError`→400 ; validation body → 422). Routes portées :

- **projects** — `GET /api/projects` · `POST /api/projects` `{slug, name?, mirror_remote?, kind?, source_url?}`
  (201 ; `source_url` → **adopte** le repo (clone son vrai historique comme SoT, `dev`/`main` normalisés) au
  lieu de semer le toolkit — via l'API = repos **publics** ; l'adoption privée avec token passe par
  `cockpit bootstrap` ; **400** si le clone échoue) ·
  `GET /api/projects/{slug}` · `PATCH /api/projects/{slug}` `{mirror_remote?}` (édite le miroir GitHub —
  `null`/vide le retire ; rend un projet GitHub-backed → un token de push devient requis ; **404** absent).
- **roadmap** — `GET /api/projects/{p}/roadmap` (features + tasks) · `POST /api/projects/{p}/features`
  `{slug, title?}` · `POST /api/features/{p}/{f}/tasks` `{slug, title?, depends_on?, priority?}` ·
  `GET /api/features/{p}/{f}/next` (résolveur DAG → `{next, n_tasks}`).
- **dispatch** — `POST /api/dispatch/{p}/{f}` (spawn worker en threadpool ; gate no-task-no-dispatch) ·
  `GET /api/jobs/{id}` (job + `tail` d'événements normalisés).
- **gate** — `POST /api/gate/{p}/{f}/review` `{findings, base?}` (verdict Tier-1 SHA-bound ; **422** si
  branche/diff absent ou diff vide — fail-closed) · `GET /api/gate/{p}/{f}` (statut review + verify,
  ancré HEAD) · `POST /api/merge/{p}/{f}` `{go, t1_override?, t15_override?}` (`run_merge` sous GO humain).
- **git** — `GET /api/projects/{p}/git` (vue **read-only** du SoT bare : `branches` `[{name, sha, subject}]`,
  `ahead_behind` `{base, head, ahead, behind}` de `main` vs `dev` — `ahead` = ce que main doit rattraper,
  `null` si dev/main pas tous deux présents — et `logs` `{ref: [{sha, subject}]}` par réf protégée). Aucune
  mutation (le cycle git vit dans `gate/merge`) ; **404** projet absent, **422** SoT illisible.
  · `GET /api/projects/{p}/git/tree?ref=&path=` (exploration read-only : entrées d'un dossier à une réf,
  `ref` défaut `dev`, `path` vide = racine → `{project, ref, path, entries:[{name, type:blob|tree|commit,
  size, sha}]}`, **dossiers d'abord** ; `size=null` pour un arbre). · `GET /api/projects/{p}/git/blob?ref=&
  path=` (contenu d'un fichier à une réf, `ref`+`path` **requis** → `{project, path, ref, size, binary,
  truncated, too_large, content}` ; gardes L4 : `too_large` si > 10 Mo (aucune lecture), `binary` si NUL
  détecté, `truncated` si > 512 Ko — **jamais d'octets bruts émis**, `content=""` pour binaire/too_large).
  Idempotent (goto-only safe) ; **404** projet absent OU réf/chemin introuvable OU `path` du mauvais type.
  · `GET /api/projects/{p}/git/commit/{sha}` (intelligence git : détail d'un commit → `{project, sha, short,
  author, email, date, subject, body, files:[{path, binary, additions, deletions}]}` ; `additions`/`deletions`
  `null` pour un binaire ; **404** sha/réf introuvable). · `GET /api/projects/{p}/git/diff?base=&head=` (diff
  unifié `base...head` three-dot → `{project, base, head, files:[…], diff}` ; `diff=""` + `files=[]` si les
  réfs sont alignées (200, pas une erreur) ; **404** une réf introuvable). · `GET /api/projects/{p}/git/
  history?ref=&path=` (commits touchant un fichier, récents d'abord → `{project, ref, path, commits:[{sha,
  short, author, date, subject}]}` ; fichier sans historique → `commits=[]` (200) ; **404** réf introuvable).
  Tous read-only, idempotents (goto-only safe).
- **onboarding** — `GET /api/onboarding` (état de config-requise : `secret_store` `{backend, ready, detail}`
  via `health()`, `requirements` `[{project, mirror_remote, needs_credential, linked, satisfied}]`,
  `complete`, `project_count`, `first_run` (aucun projet → instance neuve, le wizard guide au lieu d'annoncer
  « complet ») — aucun secret révélé) · `POST /api/projects/{p}/credential` `{token?|ref?, label?}` (lie un
  credential : `token` = voie fichier stockée → réf opaque, `ref` = voie BWS bring-your-own UUID validée ;
  réponse = projet avec `credential_ref`, **jamais le token** ; **400** mauvais usage/backend, **404** projet
  absent) · `DELETE /api/projects/{p}/credential` (délie : `credential_ref` → NULL).
- **bootstrap** — `GET /api/bootstrap` (aperçu **idempotent** de l'amorçage des outils du framework :
  `{available, tools:[{slug, source_url, kind, adopted}], adopted, total}` ; manifeste absent →
  `available:false` ; **400** manifeste invalide ; aucun secret, goto-only safe) · `POST /api/bootstrap`
  `{shared_ref?}` (adopte les outils du manifeste `<COCKPIT_HOME>/bootstrap.yaml` via P1 — **idempotent**,
  skip existants ; `shared_ref` = réf credential DÉJÀ stockée pour repos privés, absente = anonyme/public ;
  réponse `{created, skipped, failed:[{slug, error}], available}` ; manifeste absent → no-op propre).
- **docs** — `GET /api/projects/{p}/docs?ref=` (la **carte** d'un projet/outil, LUE depuis son repo/SoT bare :
  fichier canonique `docs/tool-card.md`, repli `README.md` → `{project, found, ref, path, content, truncated}`.
  `ref` optionnel (défaut : `main`→`dev`→1ʳᵉ branche). `found:false` (ni carte ni README) rendu tel quel —
  l'UI affiche un EmptyState, pas une erreur. Read-only, bare-safe (réutilise `read_blob`), idempotent. **404**
  projet absent ; **422** SoT illisible). SoT-and-derive : éditer la carte dans le repo met à jour l'affichage.
- **terminal** — `WS /ws/terminal/{project}` (PTY **local** `bash -l`, workdir borné).

Un endpoint qui borne/tronque le **signale** dans sa réponse. `WS /ws/dispatch/{job}` (streaming live du
transcript) reste **différé P5** (le `tail` par pull couvre l'observabilité V1).

## Politique de versionnage

- **Non-breaking** (ajout de colonne nullable, nouvelle route, nouveau champ optionnel) → pas de bump, note
  CHANGELOG.
- **Breaking** (renommage/suppression de colonne, changement d'enum, changement de sémantique d'un champ) →
  bump `SCHEMA_VERSION` (SQLite) / `version` (roadmap.yaml) + entrée CHANGELOG + chemin de migration.
