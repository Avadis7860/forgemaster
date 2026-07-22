# schema-contract — contrats figés du cockpit

Trois schémas sont un **contrat** : une couche produit, une autre consomme. On change une *implémentation*
librement ; changer un **schéma** exige une entrée CHANGELOG + un bump. Un schéma partiel qui se dit complet
est un bug (jamais de cap silencieux).

## 1. Schéma SQLite (`db/schema.py`, `SCHEMA_VERSION` = **11**)

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
  `NULL` → défaut résolu du `.cockpit/bundle.toml` au dispatch), `blueprint` (nullable, **v9** — **ref STAMP** :
  l'id d'un blueprint du capital central, résolu **en direct au read du board** via le client MCP, `NULL` →
  pas de blueprint), `created_at` ; unique `(project_id, slug)`.
- **`tasks`** — `id`, `feature_id`→features (cascade), `slug`, `title`, `status`
  (`todo`|`in_progress`|`done`|`blocked`|`cancelled`), `depends_on` (**JSON** : liste d'ids de tasks, DAG
  intra-feature), `priority` (`P0`..`P3`), `acceptance` (nullable TEXT, **v6** — critères de DoD injectés
  dans le prompt du worker au dispatch), `created_at` ; unique `(feature_id, slug)`.
- **`dispatch_jobs`** — `id`, `task_id`→tasks (cascade), `worktree_path`, `port` (couplé au worktree,
  nullable), `pid`, `status` (`pending`|`running`|`done`|`failed`|`killed`), `kind` (**v11** —
  `task`|`review`|`toolchain`|`fix`, défaut `task` ; **enum encodé en plein dès v11**, SQLite ne sait pas
  `ALTER` un `CHECK`), `log_path` (transcript JSONL local dérivé de `session_id`), `session_id` (session
  `claude -p` = **handle de suivi live**, v2), `num_turns`/`cost_usd`/`wall_s`/`engine` (métriques du run, v2),
  `error` (**v11** — raison d'échec courte, nullable ; le `raw` complet reste sur `log_path`, jamais recopié),
  `started_at`, `ended_at`.
- **`port_reservations`** (v2) — `id`, `project`, `purpose` (ex. `worktree:<feature>`), `port` (**unique
  global** : mono-hôte WSL), `created_at` ; unique `(project, purpose)`. Broker de ports déterministe :
  1 port stable/épinglé par worktree, relâché au teardown (spec worktree-cleanup).
- **`deployments`** (**v7** — runtime-hosting) — `id`, `project_id`→projects (cascade), `branch`
  (`main`|`dev` — **2 déploiements par projet** : prod + preview, spec project-hosting-branch-deploy-model),
  `status` (`no_deploy`|`building`|`running`|`stopped`|`unhealthy`, défaut `no_deploy` ; **enum encodé en plein
  dès v7** bien que seul `no_deploy` soit écrit — SQLite ne sait pas `ALTER` un `CHECK`, donc P5 écrira les
  autres états sans migration), `port` (service alloué par le broker deploy, nullable), `url` (endpoint servi,
  nullable), `compose_ref` (réf du compose-project = unité d'isolation, nullable), `last_deploy_sha` (nullable),
  `created_at`, `updated_at` ; unique `(project_id, branch)`. Substrat conteneur (compose) en lightweight ; le
  modèle d'identité est agnostique du substrat.
- **`non_runs`** (**v11** — trace des runs jamais lancés) — `id`, `feature_ref` (`"projet/feature"`, **pas de
  FK** : journal découplé du cycle de vie de la feature), `kind` (ce qui AURAIT couru,
  `task`|`review`|`toolchain`|`fix`), `reason` (raison de non-lancement verbatim — readiness/hold du reviewer),
  `created_at` (**ts injecté** par l'appelant). **Journal PUR** : un skip a une raison, pas un pid/port/session —
  ce n'est pas un job, et cette table n'est **jamais lue par une garde d'idempotence**.
- **`gate_verdicts`** (**v11** — historique des verdicts PAR SHA) — `id`, `project`, `feature`, `gate`
  (`review`|`toolchain`|`merge`), `sha` (HEAD ancré), `verdict` (**JSON** — payload tel qu'écrit sur disque),
  `created_at`. Là où `write_verdict` faisait un `write_text` qui **écrasait** (un rouge à T1 puis vert à T2
  perdait T1) ; l'historique par SHA préserve chaque passage. `merge` (fait-de-merge, GO humain daté) posé dans
  l'enum dès maintenant.

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

**Migration v6→v7** (runtime-hosting) : **nouvelle table `deployments`** — créée sur une base existante par le
`CREATE TABLE IF NOT EXISTS` de `create_schema` (même chemin que `port_reservations` en v2), donc **aucune
entrée `ensure_columns`** (celui-ci ne sert qu'aux *colonnes* ajoutées à une table pré-existante). Les projets
d'avant v7 obtiennent leurs 2 lignes (`main`/`dev`, `no_deploy`) au premier `list_deployments`
(`ensure_deployments`, `INSERT OR IGNORE` idempotent) ; les projets créés après v7 les reçoivent à la création.
Ajout **non-breaking**.

**Migration v7→v8** (bundle-storage-registry) : le `CHECK` figé sur `projects.project_type` est **retiré** —
l'enum des types de projet devient **registre-driven** (dérivé du filesystem par `provision.discover_types` :
ajouter un type = déposer `bundles/types/<type>/`), l'autorité de validation passant à `provision.validate_bundle`
(fail-closed) dans `registry.create_project`. SQLite ne sachant pas `ALTER` un `CHECK`, la migration fait un
**rebuild de table gardé** (`schema._migrate_v8_drop_project_type_check` : détecte le CHECK dans `sqlite_master`,
recrée `projects` sans lui, recopie ; `foreign_keys` off le temps du rebuild — id préservé, FK enfants intactes).
No-op sur une base sans CHECK (montée par ALTER) ; idempotent. Changement d'enum → **breaking, bump 7→8**.

**Migration v8→v9** (board-native blueprint) : `features` gagne `blueprint` (`TEXT`, nullable, **aucun défaut**)
via `ensure_columns` — les lignes existantes prennent `NULL` (pas de blueprint). C'est la **ref STAMP** (id d'un
blueprint du capital central) portée par une feature ; l'id est stocké brut (opaque au niveau DB/modèle) et
**résolu au read** du board via le client MCP runtime (`cockpit.mcp.blueprint_resolver`, seam
`taskmap.context`) → `GET …/roadmap` rend `{blueprint:{id, posture, resolved, reason, …}}`, dégradation honnête
si le MCP est coupé (`resolved:false` + raison, jamais inventé). Même patron additif que `facet` (v6). Ajout
**non-breaking**.

**Migration v9→v10** (deps inter-features) : `features` gagne `depends_on` (`TEXT NOT NULL DEFAULT '[]'`, liste
JSON de slugs de features) via `ensure_columns` — les lignes existantes prennent `'[]'` (aucune dépendance
inter-feature). C'est le **DAG INTER-feature**, symétrique de `tasks.depends_on` (intra) : une feature reste
non-dispatchable (`orchestrator._discoverable_features`) tant qu'une feature prérequise n'est pas `merged`. Validé
par `check` (`DANGLING_FEATURE_DEP` / `FEATURE_CYCLE` / `DEAD_FEATURE_DEP` si un prérequis est `cancelled` →
deadlock surfacé). Le prédicat « prérequis satisfait = `merged` » vit dans `resolver.classify_features` (même
moteur taskmap que le DAG des tasks, une couche au-dessus). Défaut littéral (ALTER-safe, pas de CHECK). Ajout
**non-breaking**.

**Migration v10→v11** (trace durable des échecs) : `dispatch_jobs` gagne `kind` (`TEXT NOT NULL DEFAULT 'task'`
— défaut littéral requis par `ALTER` ; les jobs existants sont tous des runs d'ouvrier → `'task'` est exact) et
`error` (`TEXT`, nullable, aucun défaut → `NULL` pour l'existant), via `ensure_columns`. Le `CHECK (kind IN …)`
vit dans le `DDL` (base neuve) ; un `ALTER` SQLite ne re-porte pas le CHECK → l'enum **complet**
(`task|review|toolchain|fix`) est posé dès maintenant pour éviter un rebuild ultérieur (précédent v8), l'invariant
étant tenu par le code qui écrit. **Deux tables neuves** — `non_runs` et `gate_verdicts` — créées sur une base
existante par `CREATE TABLE IF NOT EXISTS` (même chemin que `deployments` en v7), donc **aucune entrée
`ensure_columns`**. Ajout **non-breaking** (le bump reste obligatoire : c'est lui qui déclenche la migration —
cf. la politique de versionnage). Ce commit pose les **contenants** ; les puits d'écriture sont câblés par les
filles `cockpit-trace-job-sinks` / `cockpit-gate-verdict-history`.

**Migration v11→v12** (interview de 1ʳᵉ session) : `tasks` gagne `mode` (`TEXT NOT NULL DEFAULT 'headless'` —
défaut littéral requis par `ALTER` ; les tasks existantes sont toutes des runs headless → `'headless'` est exact),
via `ensure_columns`. Enum `headless|interactive` : une task `interactive` est **routée vers un terminal
interactif** (`cockpit interview`) par le dispatch au lieu d'un worker `claude -p` headless (une interview / un
cadrage ne se mène pas en headless). Le `CHECK (mode IN …)` vit dans le `DDL` (base neuve) ; un `ALTER` SQLite ne
re-porte pas le CHECK → l'invariant `mode∈{headless,interactive}` est tenu par le code qui écrit
(`model.add_task`). Le hint naît de la **graine** (le socle semé marque `cadrage`/`interview` `interactive`), lu
par le **dispatch générique** — zéro heuristique métier dans le moteur. Ajout **non-breaking** (le bump déclenche
la migration).

## 2. Schéma `.cockpit/roadmap.yaml` (in-repo, `roadmap/model.py`)

Versionné **avec le projet** (source de vérité côté repo), synchronisé vers la DB (index). Manifeste SEC
(noms/listes, pas de prose libre). Forme :

```yaml
version: 1
features:
  - slug: <kebab>
    title: <str>
    facet: backend|frontend|tool|doc               # v6, OPTIONNEL — facette de dispatch (omis si non posée)
    blueprint: <blueprint-id>                       # v9, OPTIONNEL — ref STAMP (omis si non posée)
    depends_on: [<feature-slug>, …]                 # v10, OPTIONNEL — DAG INTER-feature (omis si vide)
    phases: [[<task-slug>, …], [<task-slug>, …]]   # étapes ORDONNÉES ; chaque étape = ids parallèles
    tasks:
      - slug: <kebab>
        title: <str>
        priority: P0|P1|P2|P3
        depends_on: [<task-slug>, …]                # intra-feature explicite (union avec phases:)
        acceptance: <str>                           # v6, OPTIONNEL — critères de DoD injectés au prompt worker
        mode: headless|interactive                  # v12, OPTIONNEL — routage dispatch (omis si `headless`)
```

`facet:`/`acceptance:` (v6), `blueprint:` (v9), le `depends_on:` feature-level (v10) et `mode:` (v12) sont **émis
seulement si présents/non-défaut** — une roadmap sans ces champs reste identique au contrat v1 (rétro-compatible).
`mode: interactive` route la task vers le terminal interactif (`cockpit interview`) au lieu d'un worker headless. Le `depends_on:` d'une
feature est le DAG INTER-feature (non-dispatchable tant qu'une prérequise n'est pas `merged`) ; celui d'une task
reste intra-feature. `facet` tague la feature du type de travail (aligne le
worker au dispatch) ; `acceptance` porte les critères de succès de la task, rendus verbatim dans le prompt ;
`blueprint` porte la ref STAMP (id brut) de la feature, résolue en verdict au read du board (`GET …/roadmap`).

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
  `GET /api/dispatch/{p}/{f}/jobs` (historique des runs de la feature, join `tasks`) ·
  `GET /api/jobs/{id}` (job + `tail` d'événements normalisés) · `WS /ws/dispatch/{job}` (streaming live du
  transcript — **porté** ; le `tail` par pull en repli).
- **gate** — `POST /api/gate/{p}/{f}/review` `{findings, base?}` (verdict Tier-1 SHA-bound ; **422** si
  branche/diff absent ou diff vide — fail-closed) · `POST /api/gate/{p}/{f}/toolchain` (exécute le Tier-0
  natif dans le worktree, verdict SHA-bound ; **422** worktree/branche absents) ·
  `POST /api/gate/{p}/{f}/review-dispatch` (dispatche le review-worker Tier-1 ; **403** sans auth `claude`) ·
  `GET /api/gate/{p}/{f}` (statut **review + toolchain + verify** + décision composée en preview, ancré HEAD) ·
  `GET /api/gate/{p}/{f}/verdicts` (vue de lecture read-only : verdict Tier-1 COMPLET (findings) + Tier-0 natif
  (steps)) · `GET /api/gate/{p}/{f}/history` (historique des verdicts **par SHA** depuis `gate_verdicts` : ce
  qui a été rouge le reste + fait-de-merge) · `POST /api/merge/{p}/{f}` `{go, t1_override?, t15_override?}`
  (`run_merge` sous GO humain).
- **git** — `GET /api/projects/{p}/git` (vue **read-only** du SoT bare : `branches` `[{name, sha, subject}]`,
  `ahead_behind` `{base, head, ahead, behind}` de `main` vs `dev` — `ahead` = ce que main doit rattraper,
  `null` si dev/main pas tous deux présents — et `logs` `{ref: [{sha, subject}]}` par réf protégée). Aucune
  mutation (le cycle git vit dans `gate/merge`) ; **404** projet absent, **422** SoT illisible.
  · `GET /api/projects/{p}/git/tree?ref=&path=` (exploration read-only : entrées d'un dossier à une réf,
  `ref` défaut `dev`, `path` vide = racine → `{project, ref, path, entries:[{name, type:blob|tree|commit,
  size, sha, last_commit:{short, date, subject}|null}], latest_commit:{short, author, date, subject, count}|
  null}`, **dossiers d'abord** ; `size=null` pour un arbre. `last_commit` = dernier commit touchant l'entrée
  (façon GitHub ; `null` si aucun) ; `latest_commit` = dernier commit du dossier courant + nb total de commits
  (`count`), `null` si la réf/dossier est vide de commits). · `GET /api/projects/{p}/git/blob?ref=&
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
  Tous read-only, idempotents (goto-only safe). · `GET /api/projects/{p}/git/sync` (**écart SoT↔miroir
  GitHub** : **RÉSEAU, non-idempotent** — fait un `git fetch` du miroir, donc **SÉPARÉ** des lectures
  idempotentes ci-dessus, l'UI le rattache au refresh manuel, jamais au polling/goto-only → `{project, remote,
  fetched, branches:{<b>:{ahead, behind, state}}, state}`, rollup `state` et par-branche `state ∈
  {synced, local_ahead, remote_ahead, diverged}`. Dégradation **honnête, jamais 0/0 faux-vert** : miroir non
  câblé → `state=no_mirror`, injoignable/auth → `unreachable` (`fetched=false`, `branches={}`). Auth
  transitoire via le `credential_ref` du projet, jamais le token. **404** projet absent, **422** SoT illisible).
  · `POST /api/projects/{p}/git/sync/reconcile` (**la SEULE mutation git du routeur**, gatée par construction :
  réconciliation **ff-only**, jamais de merge non-ff ni `--force` — spec forge-sot-local. Preview d'ABORD via le
  `GET .../git/sync` idempotent (source unique = l'`state`), ce POST **exécute** — pas de dry-run POST. Recalcule
  la divergence puis par branche : `remote_ahead` → ff local · `local_ahead` → push ff · `diverged` → **bloqué**
  sans mutation · garde-fou : jamais ff une branche checked-out dans un worktree → `blocked_worktree`. Rend
  `{project, remote, fetched, actions:{<b>:{action, from?, to?, reason?}}, changed, blocked, state}`,
  `action ∈ {already_synced, fast_forward, pushed, push_failed, blocked_worktree, blocked_diverged}`.
  Dégradation honnête héritée (`no_mirror`/`unreachable` → aucune action). **404** projet absent, **422** op git dure).
- **tool** — `POST /api/projects/{slug}/tool/sync` (re-sync d'un **outil adopté** `kind=tool` avec son amont
  `origin`, **pull-only ff-only** — jamais de push ni de merge non-ff, frontière read-only stricte). Auto-répare
  le refspec de fetch d'un clone bare (dégèle un outil figé), fetch + avance les refs suivies (`dev`, `main`)
  quand l'amont est en avance ; après un sync qui bouge, pré-chauffe best-effort l'index Flow. Rend `{project,
  slug, kind, remote, fetched, actions:{<b>:{action, from?, to?, reason?}}, changed, blocked, state,
  index_refreshed}`, `action ∈ {already_synced, fast_forward, local_ahead_skipped, blocked_worktree,
  blocked_diverged}` (jamais `pushed` : un outil ne réécrit pas son amont). **Fail-close** : entité qui n'est pas
  un outil → **409** (un projet passe par la réconciliation gatée `reconcile`) ; entité absente → **404** ; op git
  dure → **422**. CLI miroir : `cockpit tool sync <slug>`.
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
transcript) est **porté** (déclaré dans le routeur `dispatch`, cf. ci-dessus) ; le `tail` par pull reste le
repli d'observabilité (aucune dépendance dure au WS). *(Correction : ce contrat le disait « différé P5 » —
il tourne.)*

## Politique de versionnage

**SQLite — tout changement de schéma exige un bump `SCHEMA_VERSION`, y compris un ajout de colonne nullable.**
Raison mécanique, non négociable : `db/store.py` n'appelle `create_schema` (donc `ensure_columns`) **que si**
`schema.schema_version(conn) < schema.SCHEMA_VERSION` — **le bump EST le déclencheur de migration**. Sans bump,
une base existante à v`N` ne re-rentre jamais dans `create_schema`, la colonne n'est **jamais** posée sur
l'existant, et tout `SELECT` qui la lit casse. La distinction breaking/non-breaking ne porte donc que sur le
**chemin de migration**, jamais sur la nécessité du bump :

- **Additif** (colonne nullable ou défaultée, ALTER-safe) → **bump** + `ensure_columns` (ALTER idempotent) +
  entrée CHANGELOG. (Pratique : v3, v4, v5, v6, v9 sont toutes des ajouts nullables — toutes ont bumpé.)
- **Breaking** (renommage/suppression de colonne, changement d'enum sous `CHECK`, resémantisation) → **bump** +
  **rebuild de table** (SQLite n'`ALTER` pas un `CHECK` : cf. `_migrate_v8_drop_project_type_check`) + entrée
  CHANGELOG.

**API HTTP / `roadmap.yaml`** — une nouvelle route ou un champ optionnel de payload n'a **pas** de déclencheur
de migration SQLite : entrée CHANGELOG, **pas** de bump `SCHEMA_VERSION` (le `roadmap.yaml` porte son propre
`version`, bumpé selon la même règle que ci-dessus s'il gagne un champ structurel).
