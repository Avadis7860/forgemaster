# Changelog

Format [Keep a Changelog](https://keepachangelog.com/). Un changement de **schéma** (SQLite / roadmap.yaml
/ API HTTP — cf. `docs/schema-contract.md`) est une entrée dédiée + un bump, jamais en douce.

## [Unreleased]

### Build — `allow-direct-references` pour la dép git privée `task-map`
- `pyproject.toml` : `[tool.hatch.metadata] allow-direct-references = true`. La dép runtime `task-map` est une
  référence directe (`git+https`, repo privé épinglé au SHA) ; sans cet opt-in, `pip wheel .` échoue en
  `metadata-generation-failed`. Fix build-time pur, aucun changement de comportement.

### Schéma DB v10 — DAG inter-features : `features.depends_on` (bump `SCHEMA_VERSION` 9→10)
- **Additif non-breaking** → bump `SCHEMA_VERSION = 10` + migration `ensure_columns` (ALTER idempotent). `features`
  gagne `depends_on` (`TEXT NOT NULL DEFAULT '[]'`, liste JSON de slugs de features) : le **DAG INTER-feature**,
  symétrique de `tasks.depends_on` (intra-feature). Défaut littéral `'[]'` (ALTER-safe, aucun `CHECK`) → les
  lignes existantes prennent « aucune dépendance ».
- **Sémantique** : une feature reste non-dispatchable (`orchestrator._discoverable_features`) tant qu'une feature
  prérequise n'est pas `merged` ; prédicat « prérequis satisfait = `merged` » dans `resolver.classify_features`
  (même moteur taskmap que le DAG des tasks, une couche au-dessus).
- **Validation** `check` : `DANGLING_FEATURE_DEP` / `FEATURE_CYCLE` / `DEAD_FEATURE_DEP` (prérequis `cancelled` →
  deadlock surfacé, jamais silencieux).

### Schéma DB v9 — board-native blueprint : `features.blueprint` (bump `SCHEMA_VERSION` 8→9)
- **Additif non-breaking** → bump `SCHEMA_VERSION = 9` + migration `ensure_columns` (ALTER idempotent). `features`
  gagne `blueprint` (nullable `TEXT`, aucun défaut → `NULL` pour l'existant) : la **ref STAMP** (id d'un blueprint
  du capital central) portée par une feature. Patron identique à `facet` (v6).
- **Modèle** `roadmap/model.py` : `add_feature(..., blueprint=None)` le stocke ; `_feature_doc` émet `blueprint`
  dans `roadmap.yaml` **seulement si présent** (contrat rétro-compatible, comme `facet`/`acceptance`).
- **Route** `GET /api/projects/{p}/roadmap` : chaque `features.blueprint` (ref brut) est **résolu en direct** via
  le client MCP runtime (`cockpit.mcp.blueprint_resolver` P2, seam `taskmap.context._blueprint_verdict`) →
  `{blueprint:{id, posture, resolved, reason, …champs fusionnés}}`. **Dégradation honnête** : MCP non câblé /
  coupé / vide → `resolved:false` + raison, jamais inventé. Feature sans blueprint → `blueprint: null`. Contrat
  existant (`tasks`/`state`/`blockers`/`next`) inchangé.
- **HTTP** `POST /api/projects/{p}/features` : `FeatureCreate` gagne `blueprint` (optionnel).

### Schéma DB v8 — registre de bundles registre-driven : `project_type` CHECK RETIRÉ (bump `SCHEMA_VERSION` 7→8)
- **Breaking (schéma SQLite)** → bump `SCHEMA_VERSION = 8` + migration. Le `CHECK (project_type IN (...))` figé
  sur `projects` est **retiré** : les types de projet sont désormais **découverts sur le filesystem**
  (`provision.discover_types` — un type = un dossier `bundles/types/<type>/`), et validés fail-closed par
  `provision.validate_bundle` (manifeste `.cockpit/bundle.toml` : `version`, `project_type`, `facets`,
  `default_facet` + dossiers `.claude/facets/<f>/` de support) dans `registry.create_project`.
- **Migration** `schema._migrate_v8_drop_project_type_check` : rebuild de table **gardé** (no-op si pas de CHECK)
  + **idempotent** ; `foreign_keys` désactivées le temps du rebuild (id préservé, FK enfants intactes).
- **CLI** `cockpit project create --type` : `choices` dérivés de `discover_types()` (plus de liste en dur).
- Nouveaux helpers `provision.{discover_types, read_bundle_manifest, validate_bundle}` + exception `BundleError` ;
  le manifeste `.cockpit/bundle.toml` gagne `version` (provenance amont, consommée par la sélection/stamp en P3).

### Sync miroir SoT↔GitHub — P6 : re-sync des outils adoptés **pull-only ff** (CLI + endpoint) — dégèle le CT
- **Nouvelle sous-commande CLI** `cockpit tool sync <slug>` (`cli.py` + `toolsync.py`) **et** route HTTP
  (non-breaking → note, pas de bump `SCHEMA_VERSION`) : `POST /api/projects/{slug}/tool/sync` → `{project,
  slug, kind, remote, fetched, actions:{<b>:{action, from?, to?, reason?}}, changed, blocked, state,
  index_refreshed}`. Re-fetch un **outil adopté** (`kind=tool`) et avance ses refs suivies (`dev`, `main`)
  quand l'amont GitHub a pris de l'avance — **pull-only, ff-only, jamais de push** (frontière read-only stricte).
- **`InternalGit.sync_tracking`** (nouvelle primitive, **PAS** sur le Protocol figé — op spécifique au clone bare
  d'un outil, contrairement à `reconcile` qui est un concept forge partagé ; ajouter au contrat forcerait un stub
  vide de sens sur `GitHubGit`) : recalcule la divergence puis par branche — `remote_ahead` → **ff** local ;
  `local_ahead` → `local_ahead_skipped` (**jamais de push amont** : un outil read-only n'a aucune autorité
  d'écriture) ; `diverged` → `blocked_diverged` ; garde-fou worktree (`blocked_worktree`) et dégradation honnête
  (`unreachable`/`no_mirror`) hérités.
- **Correctif de fond `ensure_fetch_refspec`** : un `git clone --bare` (voie d'adoption, `clone_sot`) NE POSE
  AUCUN refspec de fetch → `git fetch origin` échouait à peupler `refs/remotes/origin/*` (les 4 outils du rail
  étaient donc **infetchables**, pas seulement figés). `sync_tracking` **auto-répare** le refspec avant de fetcher
  → dégèle aussi les clones déjà adoptés sans les ré-adopter.
- **Fail-close symétrique** : la route/CLI ne cible QUE `kind=tool`. Un **projet** (SoT autoritatif) la **refuse**
  → **409** (`NotAToolError`, intercepté avant le handler `ValueError→400` global) : sa voie de sync est la
  réconciliation gatée `reconcile` (P5), jamais ce pull-only descendant.
- **Fraîcheur Flow gratuite** : l'index code-map est **caché par (SHA, schema)** → un `dev` avancé reconstruit
  au prochain accès Flow (aucune « bust » : le symptôme « Flow périmé » venait du SoT jamais re-fetché). On
  **pré-chauffe** best-effort après un sync qui bouge (`index_refreshed`) — jamais bloquant (code-map absent →
  `False`, honnête).
- **Verrous** : primitive (ff remote_ahead → synced · local_ahead **jamais poussé** · diverged bloqué · garde-fou
  worktree · self-heal refspec bare-clone · dégradation unreachable) ; module (fail-close projet → 409 · absent →
  KeyError · pré-chauffe best-effort · no-change n'appelle pas l'index) ; endpoint TestClient (ff + already_synced
  · 409 projet · 404 absent) ; CLI (codes de sortie).

### Sync miroir SoT↔GitHub — P5 : réconciliation un-clic **ff-only** gatée (route + primitive + UI)
- **Le contrat figé `GitBackend` gagne une méthode** (`git/backend.py`, Protocol runtime-checkable) :
  `reconcile(sot, *, remote, branches, creds_ref=None) -> dict`. Extension de contrat (note dédiée, cf.
  `docs/schema-contract.md`) ; `GitHubGit` la stub (P6, `NotImplementedError`), `InternalGit` l'implémente.
- **Nouvelle route HTTP** (non-breaking → note, pas de bump `SCHEMA_VERSION`) : `POST
  /api/projects/{p}/git/sync/reconcile` → `{project, remote, fetched, actions:{<b>:{action, from?, to?,
  reason?}}, changed, blocked, state}`. **Preview d'ABORD via le `GET .../git/sync` idempotent** (source unique
  = l'`state` par branche) ; ce POST **exécute** — jamais un dry-run POST (doctrine preview-gated-via-GET).
- **Réconciliation ff-only, jamais de merge non-ff ni de `--force`** (`InternalGit.reconcile`) : recalcule la
  divergence (l'état frais fait autorité) puis agit **par branche** : `remote_ahead` → **ff** local vers la
  ref de suivi (`merge_ff`) ; `local_ahead` → **push ff** cette seule branche (refspec explicite, best-effort) ;
  `diverged` → **bloqué**, aucune mutation (spec forge-sot-local : jamais d'auto-merge). La granularité
  par-branche réconcilie chaque branche ff-able même quand le rollup projet est `diverged` (cross-branch).
- **Garde-fou worktree** : on ne ff **jamais** une branche checked-out dans un worktree actif (`branch -f` la
  refuserait) → `blocked_worktree`, aucune mutation. **Dégradation honnête** héritée : `no_mirror`/`unreachable`
  → `fetched=False`, aucune action. Auth transitoire partagée (`_authed_env`, factorisé avec `fetch_remote`).
- **UI (`web/`)** : bouton « ⟳ Réconcilier » (en-tête GitTab, visible sur une divergence réelle) → **panneau
  inline preview** (plan dérivé de l'état : `dev → ff depuis GitHub (+N)`, `main → à jour`) → **Confirmer
  (ff-only)** → résultat par branche → re-fetch du badge. Un état tout-divergé n'offre pas d'exécution : il
  **explique** la résolution manuelle (Alert danger). Hook mutation `useReconcileSync` (invalide la vue git +
  le rail ; le badge sync enabled:false est re-fetché à la main). Tons `reconcileTone` — blocages/échecs
  **jamais verts** (anti-faux-succès).
- **Verrous** : primitive (ff remote_ahead → synced · push local_ahead → miroir reçu · diverged bloqué sans
  mutation · garde-fou worktree · cross-branch réconcilié indépendamment · dégradations) ; endpoint TestClient
  (ff → badge `synced` · vraie divergence bloquée sans mutation · 404) ; vitest (plan/reconcilable/outcome/ton)
  ; **boucle visuelle** (badge « GitHub +2 » + panneau preview `ff depuis GitHub (+2)` / `à jour` rendus).

### Sync miroir SoT↔GitHub — P3 : endpoint `GET /api/projects/{p}/git/sync` (réseau, dédié)
- **Nouvelle route HTTP** (non-breaking → note, pas de bump `SCHEMA_VERSION` — cf. politique de versionnage) :
  `GET /api/projects/{p}/git/sync` expose `InternalGit.remote_divergence` (P2) → `{project, remote, fetched,
  branches:{<b>:{ahead, behind, state}}, state}`. Auth transitoire via le `credential_ref` du projet (résolu
  à l'usage par le `cred_resolver` du store actif, jamais le token).
- **Séparé du `GET .../git` idempotent** : `/git/sync` fait un `git fetch` **réseau, non-idempotent** — il ne
  doit PAS être atteint par le runner de boucle visuelle goto-only ni par du polling ; l'UI (P4) le rattache
  au refresh manuel. `/git` reste la vue read-only bare-safe (branches, log, ahead/behind local-vs-local).
- **Dégradation honnête** propagée telle quelle : `no_mirror` (miroir non câblé), `unreachable` (fetch KO) —
  jamais 0/0 faux-vert. Projet absent → 404 ; SoT illisible → 422. Verrou e2e (TestClient sur SoT réel +
  miroir bare cloné) : `test_git_sync_endpoint_reports_divergence_and_degrades` (no_mirror → synced →
  remote_ahead après avance du miroir → 404).

### Sync miroir SoT↔GitHub — P2 : primitive `remote_divergence` (**bump du contrat `GitBackend`**)
- **Le contrat figé `GitBackend` gagne une méthode** (`git/backend.py`, Protocol runtime-checkable) :
  `remote_divergence(sot, *, remote, branches, creds_ref=None) -> dict`. Bump de contrat au sens de la
  politique de versionnage (`docs/schema-contract.md`) : entrée dédiée, jamais en douce. `GitHubGit` stub le
  reste cohérent (P6, `NotImplementedError`) ; `InternalGit` l'implémente.
- **Écart SoT↔remote par branche + rollup projet, avec dégradation honnête** (`InternalGit.remote_divergence`,
  à côté d'`ahead_behind`) : `fetch` best-effort (auth transitoire réutilisée de P1) puis
  `rev-list --left-right --count <remote-ref>...<local-ref>` par branche → `{ahead, behind, state}` avec
  `state ∈ {synced, local_ahead, remote_ahead, diverged}`. Renvoie
  `{remote, fetched, branches, state}` où `state` est le rollup projet.
- **Jamais de 0/0 faux-vert** (invariant central) : remote non configuré → `no_mirror` ; fetch échoué
  (injoignable/auth) → `unreachable` — dans les deux cas `fetched=False` + `branches={}`. Une branche absente
  d'un côté est un écart réel (`local_ahead`/`remote_ahead`), pas `synced`. Des branches qui tirent en sens
  opposés (l'une local-avance, l'autre remote-avance) roulent en `diverged` (aucun ff unique) — la
  réconciliation reste une op **séparée** (P5), la primitive ne mute pas le SoT.
- Classifieurs **purs testables** extraits (`_branch_sync_state`, `_rollup_sync_state`). Verrous :
  conformité au contrat étendu (`test_internal_git_satisfies_extended_backend_contract`) + matrice d'états
  (synced / remote_ahead / local_ahead / non-ff sur une branche / rollup cross-branch `diverged` /
  branche absente du remote / dégradations `no_mirror` + `unreachable`).

### Sync miroir SoT↔GitHub — P1 : remote fetchable + fetch authentifié (prérequis)
- **Le miroir se matérialise enfin dans git** : `mirror_remote` n'était qu'une string en DB (jamais un
  `git remote`). `registry.create_project` (projet seedé) et `registry.set_mirror_remote` câblent désormais
  le remote `mirror` sur le SoT bare (`add`/`set-url`, retrait sur `None`). Les entités adoptées gardent leur
  `origin` (posé par `clone_sot`) — projets ⟶ `mirror`, outils ⟶ `origin`.
- **Trois primitives `InternalGit`** (hors contrat figé `GitBackend` — le bump est réservé à P2/`remote_divergence`) :
  `set_remote(sot, name, url)` (idempotent), `remove_remote(sot, name)` (best-effort), et
  `fetch_remote(sot, remote, *, creds_ref=None) -> bool` — fetch **best-effort** (False sur remote absent/
  injoignable/auth, ne lève jamais) avec **auth transitoire** : le token est résolu à l'usage via le
  `cred_resolver` injecté et injecté par `credential_env` (jamais persisté, jamais en argv), `GIT_TERMINAL_PROMPT=0`.
- Verrous : `test_set_remote_is_idempotent_add_then_seturl`, `test_remove_remote_is_best_effort`,
  `test_fetch_remote_updates_tracking_and_resolves_creds_transiently`,
  `test_fetch_remote_missing_remote_returns_false_without_raising`.

### Consommateur code-map : cache (SHA, schema_version) + découplage du layout interne (code-map P6)
- **Clé de cache d'index enrichie de `schema_version`** (`codemap/index.py`) : le dossier dérivé passe de
  `home/codemap/<projet>/<sha>` à `home/codemap/<projet>/<sha>/<schema>`. Un upgrade de code-map (nouveau
  contrat : `schema_version` différent, lu via `codemap --schema-version` **sans index**) ouvre un dossier
  neuf → **rebuild automatique**. Ferme le trou « il fallait vider le cache à la main après un déploiement
  de code-map » (l'ancien index n'est jamais servi périmé). Verrou : `test_index_cache_key_includes_schema_version`.
- **Découplage du nom de fichier interne de code-map** : le cache-hit n'inspecte plus `.codemap/calls.manifest.json`
  (organe interne de code-map) mais un **marqueur propre au cockpit** (`.cockpit-index-built`) écrit après un
  build réussi. On dépend du **contrat** de code-map (rc, `--schema-version`, stdout JSON), plus de son
  arborescence — code-map peut réorganiser ses fichiers internes sans casser le cockpit.

### Indicateur d'auth Claude dans l'UI — l'état d'auth devient visible
- **`ClaudeAuthStatus.tsx`** (nouveau) : composant réutilisable qui rend l'état `claude_auth` du GET
  onboarding — `ClaudeAuthBadge` (pastille inline) + `ClaudeAuthBlock` (encart avec l'instruction
  `claude login` quand l'auth manque, miroir exact du `AUTH_HINT` backend). Présence, jamais la valeur.
- **Wizard 1er-démarrage** : nouvelle étape « Compte Claude » (connecté via *source* / non connecté →
  `claude login`). Répond à la surprise « il ne m'a rien demandé » : l'usage n'est plus silencieux.
- **Onglet Dispatch** : pré-flight — sans auth Claude, le bouton « Dispatcher » est **désarmé** et le bandeau
  s'affiche **avant** le clic (au lieu d'un 403 après coup). Le backend reste l'autorité (fail-closed serveur).
- **Schéma** : `OnboardingStatusSchema` gagne `claude_auth` (front zod, miroir du champ backend). +2 vitest.

### Gate d'auth Claude explicite — jamais d'usage silencieux d'un compte hérité
- **`cockpit/auth.py`** (nouveau) : `claude_auth_status(home, env)` détecte de façon **déterministe** si
  l'hôte est authentifié pour spawner des workers `claude` — présence de `$HOME/.claude/.credentials.json`
  ou d'une clé d'env (`ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`), **sans jamais lire la valeur**.
- **Gate dur aux 3 points d'entrée** (`cockpit dispatch`, `cockpit run`, `POST /api/dispatch`) : refus
  **AVANT tout spawn** si non authentifié — CLI exit 2 + message actionnable (`claude login` dans le
  terminal), API `403`. Fini l'héritage silencieux de l'auth du host (perçu comme un contournement d'auth) ;
  un install neuf ne travaille qu'après un `claude login` **explicite** de l'utilisateur, sur **son** compte.
- **Onboarding** : `status()` porte un champ `claude_auth` (axe **orthogonal** à `complete`) pour surfacer
  l'état d'auth au wizard. Le cockpit n'embarque, ne partage ni n'injecte aucun credential : auth **par
  machine** via le CLI officiel. +8 tests (détection, surface, refus CLI/API, gate laisse passer si authed).

### Contenu méthodologique semé (P1 cockpit-typed-bundles, Phase 6)
- **Deux skills de méthodo dans `bundles/base/.claude/skills/`** (⇒ semés dans TOUT projet, tout type) :
  `roadmap-decompose` (intention → features[facette] → tasks[`depends_on` DAG + `acceptance`] : ce qui rend le
  travail dispatchable, séquencé, parallélisable) et `docs-authoring` (rédiger la mémoire `docs/` :
  audience-first, intention avant mécanique, tenue interrogeable par `docsmap`). Ce sont **les sections que le
  projet sait remplir seul** — au-delà de la boucle git (`work-loop`/`quality-gate`). Le CLAUDE.md socle et
  `docs/architecture.md` les référencent (planifier → exécuter → mémoriser). Données pures, gate inchangé.
- +4 tests (`test_provision.py`) : les deux skills présents et non vides dans chaque type + référencés dans le
  CLAUDE.md ; chaque type porte une `architecture.md` non-stub (« Comment ce projet se travaille » présent).

### Orchestrateur parallèle — cœur `run_project` (P1 cockpit-typed-bundles, Phase 4)
- **`dispatch/orchestrator.py`** (nouveau) : `run_project(conn, settings, *, project, max_parallel=2, git,
  runner)` draine la roadmap et **parallélise les features indépendantes prêtes** (feature = worktree =
  mutex ; N features prêtes ⇒ N workers). `ThreadPoolExecutor`, `in_flight` muté **à la soumission** (seul
  le thread principal assigne → zéro double-dispatch), `_dispatch_one` en **connexion par thread** (réutilise
  `dispatch_next` intact). **La boucle possède la transition `done`** (le dispatch mono laisse `in_progress`) :
  sans elle le résolveur ne ferait jamais avancer le DAG. Tolérance à l'échec (feature KO → task revenue
  `todo` + exclue du run) ; terminaison garantie (le travail restant décroît strictement).
- +5 tests (`test_orchestrator.py`, DB fichier + git réel + runner injecté qui mesure la concurrence) :
  drainage DAG intra-feature, parallélisme borné (`peak==max_parallel`), mutex par feature (`feature_peak==1`),
  isolation d'échec, terminaison. **Pas** de merge auto : la boucle ne produit que des commits sur branches.
- **CLI `cockpit run <project> [--max-parallel N]`** (Phase 5) : `orchestrator.cli_dispatch` draine et imprime
  le rapport (dispatchées / ok / échouées / drainé) ; exit 0 si drainée sans échec, 1 sinon (relance
  reprend là où ça a bloqué). `cockpit dispatch <feature>` (mono) inchangé. +2 tests (parse + smoke rapport).

### Facette de feature — activation + prompt (P1 cockpit-typed-bundles, Phase 3)
- **`provision/facet.py`** (nouveau) : `resolve_facet(root, feature_facet)` (feature.facet → default_facet du
  `.cockpit/bundle.toml` → fallback `doc`) et `activate_facet(wt, facet)` (copie
  `.claude/facets/<f>/settings.local.json` → `.claude/settings.local.json` gitignoré). Déterministe, fail-soft.
- **`dispatch/worktree.reserve`** : active la facette de la feature DANS la worktree après checkout (hooks/
  permissions du type de travail ; idempotent).
- **`roadmap/prompt.build_worker_prompt`** : injecte **persona + méthode** de la facette (lues des `.md`
  committés) + les **critères d'acceptation** de la task (`## Critères d'acceptation (DoD)`). Un même worker
  passe du backend au frontend en étant **ré-aligné par la facette de sa feature**.
- **CLI/API** : `roadmap add-feature --facet`, `task add --acceptance` ; `FeatureCreate.facet`,
  `TaskCreate.acceptance`. +9 tests (activation gitignorée, injection persona/méthode/critères, fail-soft).

### Bundles par type — `create --type` (P1 cockpit-typed-bundles, Phase 2)
- **`provision/`** : `payload/` → `bundles/base/` (git mv) + `bundles/types/{service-api,cli-tool,front-ts}/`
  (overlays). `load_bundle(project_type)` compose **base ⊕ overlay** (whole-file : `base | overlay`,
  déterministe) ; `load_payload()` = shim `load_bundle("generic")`. Chaque bundle porte `.cockpit/bundle.toml`
  (facettes + default_facet) et des facettes `.claude/facets/<f>/` (PERSONA/METHOD/settings.local.json).
- **`projects/registry.create_project(project_type=…)`** : valide l'enum (re-check du CHECK DDL) + persiste
  + sème `load_bundle(project_type)`. CLI `project create --type {generic|service-api|cli-tool|front-ts}` +
  route `POST /api/projects` (`ProjectCreate.project_type`).
- Design DRY : les overlays surchargent `docs/architecture.md` (identité du type) + ajoutent leurs facettes,
  **sans dupliquer** le `CLAUDE.md` commun (base reste la source unique du contrat). +10 tests (compose,
  override whole-file, déterminisme, cohérence facettes↔dossiers, seed typé).

### Schéma **v6** — fondations typed-bundles (P1 cockpit-typed-bundles, Phase 1)
- **`db/schema.py`** `SCHEMA_VERSION` **5 → 6** : `projects` gagne `project_type`
  (`generic|service-api|cli-tool|front-ts`, défaut `generic`, `CHECK` en DDL + validé par
  `registry.create_project`) — le bundle semé à la création ; `features` gagne `facet` (nullable :
  `backend|frontend|tool|doc`, la facette de dispatch qui aligne le worker) ; `tasks` gagne `acceptance`
  (nullable TEXT : critères de DoD injectés dans le prompt worker). Migration `ensure_columns` (ALTER
  idempotent), non-breaking. Cf. `docs/schema-contract.md` (SQLite + roadmap.yaml).
- **`db/store.py`** : `PRAGMA busy_timeout=5000` (prérequis de l'orchestrateur parallèle à venir — évite
  `SQLITE_BUSY` entre connexions-par-thread ; bénin pour le mono).
- **`roadmap/model.py`** : `add_feature(facet=…)` (validé contre `FACETS`), `add_task(acceptance=…)`,
  `to_yaml` émet `facet`/`acceptance` **seulement si présents** (contrat roadmap.yaml rétro-compatible).

### Claude Code dans le terminal web — `provision-ct.sh --with-claude` (P1 cockpit-workspace-ux)
- **`deploy/provision-ct.sh`** gagne un flag opt-in `--with-claude` (défaut off) : une étape d'install (recette
  renumérotée `[n/7]`) pose le CLI `claude` via l'**installeur natif officiel** (`claude.ai/install.sh`, binaire
  autonome — **aucun Node, aucune clé API**) dans le `~/.local/bin` de l'utilisateur du service (propriétaire du
  PTY du terminal). Durci : download-puis-exec (jamais `curl | bash`), retry x2, guard PATH `~/.profile`+`~/.bashrc`
  (le PTY lance `bash -l`), idempotent (skip si présent), vérif dure `claude --version`. Login OAuth au 1ᵉʳ run.
- **`docs/install.md`** : section « Claude Code dans le terminal web ». Aucun changement de schéma.

### Recette CT reproductible — dernière version + batteries incluses (P3 cockpit-batteries-included)
- **`deploy/build-wheel.sh`** (mainteneur, Node build-time) : build la SPA puis `pip wheel --no-deps` **depuis
  HEAD** (jamais un snapshot en retard, D5) ; garde-fou qui vérifie que l'UI est bien embarquée dans le wheel
  (`cockpit/_web_dist/index.html`).
- **`deploy/provision-ct.sh`** (hôte cible, Python seul, aucun Node) : venv → install du wheel → `install-service`
  → dépôt du manifeste → **`cockpit bootstrap`** → activation systemd, **en une commande**. Idempotent
  (ré-exécution sûre), fail-loud, imprime chaque étape, aucun secret en argv (token via `--token-file`).
- **`deploy/bootstrap.yaml`** : l'**édition maintainer** livrée — les 5 outils du framework (`cockpit`,
  `code-map`, `front-map`, `docs-map`, `mcp-catalogs`) en `kind=tool`. Donnée versionnée, aucun secret ;
  gate-protégée par un test qui la fait relire par le vrai `load_manifest`.
- **`docs/install.md`** : section « Édition maintainer — recette CT reproductible » (build → provision →
  publier plus tard sans changement de code, D7).
- Aucun changement de schéma (scripts + docs + donnée d'édition).

### Amorçage des outils du framework — manifeste + `cockpit bootstrap` + étape wizard (P2 cockpit-batteries-included)
- **Module `bootstrap.py`** : lit un manifeste `<COCKPIT_HOME>/bootstrap.yaml` (édition maintainer, SEC,
  aucun secret) et **adopte** chaque outil via P1 (`create_project(source_url=…)`), classé `kind=tool` (rail
  section « Outils »), miroir câblé sur la source. **Idempotent** (slug présent → `skipped`) ; échec d'une
  entrée isolé (`failed`, la boucle continue) ; manifeste **absent → no-op** propre ; manifeste **invalide →
  abort** (fail-loud). Credential **par entrée** (`credential_ref`, un token de lecture par repo) avec repli
  sur un `shared_ref` partagé, sinon clone anonyme (repos publics, forward-compatible).
- **CLI `cockpit bootstrap`** : `--init` écrit un gabarit (garde no-overwrite) ; `--token-file <f>` lie un
  token de lecture partagé (voie fichier → store, jamais en argv/DB/manifeste). Sortie 1 s'il reste des échecs.
- **Schéma HTTP** : `GET /api/bootstrap` (aperçu **idempotent** goto-only : `{available, tools:[{slug,
  source_url, kind, adopted}], adopted, total}`) · `POST /api/bootstrap` `{shared_ref?}` (exécute ;
  `{created, skipped, failed, available}`). `docs/schema-contract.md` §2b + §3 à jour.
- **Wizard `/setup`** : étape « Outils du framework » (shallow : installer la boîte à outils / ignorer) —
  liste les outils du manifeste + « Installer la boîte à outils (N) » d'un clic ; manifeste absent = note
  générique (wizard intact). `useBootstrap`/`useRunBootstrap` + schémas Zod. Boucle visuelle : rendu vérifié.
- Aucun bump `SCHEMA_VERSION` (route + fichier-manifeste additifs, aucune colonne).

### Adopter un dépôt existant — clone au lieu de semer (P1 cockpit-batteries-included)
- **Primitif bare-safe** `InternalGit.clone_sot(sot, url, *, creds_env=None)` : `git clone --bare` du repo
  distant (son vrai historique) ; auth **optionnelle** via `credential_env` (privé → token transitoire ;
  public → anonyme, forward-compatible) ; `_normalize_forge_branches` garantit `dev`+`main` (synthèse depuis
  `master`/`main`-only). Le résolveur de credential `cred_resolver(settings)` est **remonté** dans
  `secrets/` (partagé merge + registry).
- **`create_project(..., source_url=…)`** : branche le SoT sur un **clone** au lieu du seed, en **clone/insert
  atomique** (un clone échoué ne laisse aucune row → reprise propre ; échec → `ValueError`/400 avec hint).
- **Schéma (v4→v5)** : `projects.source_url` (nullable, provenance d'un projet adopté ; métadonnée, jamais un
  secret). `POST /api/projects` accepte `source_url?` (repos publics via l'API) ; CLI `cockpit project create
  --from <url>`. `docs/schema-contract.md` §1 + §3 à jour.
- **`ui_shot.py --click TEXTE[#N]`** (répétable, séquentiel) : joue des gestes **read-only** après le goto,
  avant la capture, pour rendre LIVE une surface pilotée par un state React sans route (détail de commit,
  visionneuse, historique). S'appuie sur le champ additif `clicks` du runner `render_check.js` (usage strict :
  n'ouvrir que du read-only, jamais un geste mutant). A servi l'acceptance P4 (les 3 pièces click-gated
  capturées LIVE + Read). Aucun changement de schéma/route.

### Intelligence git read-only — détail de commit + diff de feature + historique fichier (P3 git-repo-explorer)
- **Primitives bare-safe** dans `git/internal.py` : `commit_detail(sot, sha)` (métadonnées + fichiers touchés
  avec `+/-` par fichier, `null` pour un binaire) et `file_history(sot, ref, path)` (commits touchant un
  fichier, récents d'abord). Le diff `base...head` réutilise `diff_text`/`diff_names` déjà écrits.
- **Schéma HTTP** : `GET /api/projects/{p}/git/commit/{sha}`, `GET /api/projects/{p}/git/diff?base=&head=`
  (diff unifié three-dot ; `diff=""` si réfs alignées — 200), `GET /api/projects/{p}/git/history?ref=&path=`
  (fichier sans historique → `[]` — 200). Tous read-only, idempotents (goto-only safe) ; **404** projet/réf/
  sha introuvable. `docs/schema-contract.md` §git mis à jour.
- **Front** : détail de commit (clic sur une entrée de log/branche), **Diff de feature** rendu unifié coloré
  (base/head réutilisant les branches chargées ; tokens sémantiques, aucune teinte inline), historique par
  fichier (basculeur dans la visionneuse). Aucune mutation.

### Explorateur de dépôt read-only — arbre + contenu (P1 git-repo-explorer)
- **Primitives bare-safe** dans `git/internal.py` : `ls_tree(sot, ref, path="")` (entrées d'un dossier à une
  réf, dossiers d'abord, via `ls-tree --long <ref>:<path>`) et `read_blob(sot, ref, path)` (contenu d'un
  fichier via `cat-file`, **octets bornés**). Gardes L4 : `too_large` au-delà de 10 Mo (aucune lecture),
  `binary` si NUL détecté, `truncated` au-delà de 512 Ko — **jamais d'octets bruts émis**. Read-only,
  bare-safe (ni index ni working-tree).
- **Schéma HTTP** : `GET /api/projects/{p}/git/tree?ref=&path=` et `GET /api/projects/{p}/git/blob?ref=&path=`
  (idempotent, goto-only safe ; **404** projet/réf/chemin introuvable). Aucune mutation. `docs/schema-contract.md`
  §git mis à jour.

### Production serve — service systemd + `docs/install.md` (P3 turnkey-install)
- **`cockpit install-service`** (nouvelle sous-commande) : génère une unité systemd pour `cockpit serve` —
  portée **user** (défaut, sans root, `~/.config/systemd/user/`) ou **`--system`**. Écrit aussi un
  `cockpit.env` gabarit (store/bind, jamais un secret ; conf existante préservée) et **imprime** les
  commandes `systemctl` (n'exécute pas systemctl → pas de footgun privilège). `Environment=HOME` épinglé
  (sinon git ne lit pas le helper de credentials). Module pur `cockpit.service` + gabarit manuel
  `deploy/cockpit.service`.
- **`docs/install.md`** : guide turnkey self-hosted — wheel packagé (aucun Node) vs sources, wizard 1er
  démarrage, service systemd + note reverse-proxy/TLS (pas d'auth intégrée), coffre file/BWS, mise à jour.
  README + index docs mis à jour.

### Wizard : le token de push vit dans Réglages, pas dans le wizard (retour terrain P2)
- Retrait de l'étape « Miroir GitHub & token » du wizard `/setup` : elle ne gérait que les projets déjà
  à-miroir-sans-token et ne permettait pas d'ajouter un miroir à un projet fraîchement créé → cul-de-sac.
  La gestion **miroir + token par repo** reste dans **Réglages** (surface complète, éditable à tout moment).
  Le wizard s'y contente d'un **renvoi** quand un token de push est en attente ; le **bandeau** « token requis »
  pointe désormais vers **Réglages** (et non plus le wizard).

### Wizard 1er-démarrage guidé (`/setup`) + first-run (P2 turnkey-install)
- **`GET /api/onboarding`** gagne `project_count` + **`first_run`** (aucun projet → instance neuve). Corrige
  le faux « complet » sur une instance vide : le wizard **guide** (« crée ton 1er projet ») au lieu d'annoncer
  qu'il n'y a rien à faire. `onboard status` (CLI) affiche aussi l'invite 1er-démarrage.
- **Wizard `/setup`** (`SetupWizard`) : page guidée à étapes vivantes — bienvenue → coffre de secrets (backend
  + `health` + hint BWS) → **créer ton 1er projet** → miroir GitHub + token (optionnel) → prêt. **Non
  bloquant**, quittable, ré-ouvrable. Il **séquence** les affordances existantes (il ne les réécrit pas) :
  `NewProjectForm` (extrait du rail, **source unique** de création), `MirrorForm`, `CredentialForm`.
- **Surfaçage first-run** : le bandeau du shell invite à `/setup` sur instance neuve ; la Landing affiche une
  carte de bienvenue → wizard ; le bandeau « incomplet » et le rail « à régler » pointent aussi `/setup`.
- **Distribution turnkey** : l'utilisateur final n'installe **que Python**. Le hook de packaging
  `hatch_build.py` **force-include `web/dist`** dans le wheel sous `cockpit/_web_dist` — `pip install <wheel>
  && cockpit serve` sert la SPA **sans Node requis**. Le front se build via `npm run build`/`cockpit setup`
  **avant** de packager (le hook ne lance pas npm : éviter le footgun `pip install -e`). Dist **jamais**
  re-committée (respecte `docs/specs/web-cockpit-spa.md`).
- **`web_dist_dir()`** cherche désormais dans l'ordre : `COCKPIT_WEB_DIST` → dist empaquetée
  (`cockpit/_web_dist`, wheel) → layout source (`web/dist`, dev).
- **`cockpit setup`** (nouvelle sous-commande) : build l'UI depuis les sources (from-clone) ; **fail-loud**
  avec instructions si Node/npm absent ; no-op sur une install wheel (UI déjà incluse). Module réutilisable
  `cockpit.webbuild`.
- **`_mount_spa` fail-loud** : dist absente → **page d'aide à `/`** (« UI non buildée → `cockpit setup` ou
  wheel packagé ») + warning au log, **au lieu d'un 404 muet**. L'API (`/api`, `/health`) reste valable.

### Projet GitHub-backed depuis l'UI : config du miroir + token (phase 4c-2, suite)
- **`registry.set_mirror_remote`** + route **`PATCH /api/projects/{slug}` `{mirror_remote?}`** (édite le
  miroir GitHub d'un projet existant ; `null`/vide le retire). Créer un projet à l'UI puis le rendre
  GitHub-backed sans passer par la CLI. Un miroir posé rend un token de push *requis*.
- **Front** : champ « miroir GitHub (optionnel) » au formulaire de création ; `MirrorForm` (configure/édite/
  retire le miroir, partagée Réglages + vue projet) ; le panneau Réglages et la carte Git montrent désormais
  **Miroir** (toujours éditable) puis **Token** (une fois le miroir posé) ; hooks `useSetMirror` +
  `api.updateProject`. Corrige le trou remonté : un projet créé à l'UI (local-only) pouvait afficher « aucun
  miroir » sans aucune voie pour ajouter un secret.

### Onboarding — wizard web : bandeau + panneau Réglages + token/repo (phase 4c-2, front)
- **Bandeau non bloquant** (`OnboardingBanner`) dans le shell : rappelle une config incomplète (coffre
  injoignable ou N tokens de miroir requis) → renvoie vers Réglages. Le cockpit reste utilisable.
- **Panneau Réglages** (`/settings`, `SettingsTab`) : carte racine du coffre (backend + `health` + état) +
  liste des credentials par repo (lié / requis / aucun miroir) avec l'affordance de liaison.
- **Affordance token/repo** (`CredentialForm`, partagée) : **backend-aware** — voie fichier (token masqué,
  `type=password`) vs voie BWS (UUID). Un token lié n'affiche QUE sa référence tronquée, jamais la valeur.
  Réutilisée sur la vue projet (`ProjectCredentialCard`, onglet Git — là où vit le push/mirror).
- **Data layer** : `ProjectSchema` gagne `credential_ref` ; schémas `OnboardingStatus`/`SecretStoreHealth`/
  `OnboardingRequirement` + `CredentialLinkInput` ; hooks `useOnboarding`/`useLinkCredential`/
  `useUnlinkCredential` (invalident onboarding + projets + projet — source unique Python, jamais deviné).
- Boucle visuelle : `ui_shot.py` seede un état credential mixte (1 lié / 1 requis) → routes vérifiées au
  screenshot. Gate front vert (eslint + vitest 26 + tsc/build) + `front_conformance` OK.

### Onboarding — check config-requise + `cockpit onboard` + routes credential (phase 4c-1, backend)
- Nouveau module **`src/cockpit/onboarding.py`** : `status()` (racine du store joignable via `health()` +
  exigences par projet — un projet à `mirror_remote` a **besoin** d'un token ; `complete` sans faux-vert) et
  `link_credential()` / `unlink_credential()`. Deux voies unifiées : **fichier** (`token` → `store.put` → réf
  opaque) et **BWS** (`ref`/UUID bring-your-own, validé via `store.get` avant liaison). La DB ne reçoit que
  la **référence** ; le store la valeur — jamais de token en log/argv/retour d'API.
- **`SecretStore.health()`** (nouvelle méthode du Protocol) : racine de confiance joignable ? `file` =
  zéro-config (toujours prêt) ; `bws` = prêt ssi `BWS_ACCESS_TOKEN` se résout (check **local**, aucun login
  réseau, ne révèle pas le token).
- **CLI `cockpit onboard`** : `status` (défaut — ce qui manque, exit 1 si incomplet), `link <project>
  --token-file <f>` (jamais le token en argv) `| --ref <uuid>` `[--label]`, `unlink <project>`.
- **API** : `GET /api/onboarding`, `POST /api/projects/{p}/credential` `{token?|ref?, label?}` (réponse =
  `credential_ref`, jamais le token ; 400/404), `DELETE /api/projects/{p}/credential`. `Deps.secret_store()`
  expose le store actif par injection.

### credential_ref par entité + résolution au writeback (phase 4b — onboarding self-hosted)
- **Schéma SQLite v4** (bump `SCHEMA_VERSION=4`) : `projects` gagne `credential_ref` (`TEXT`, nullable,
  aucun défaut → `NULL` rétroactif). Migration en place idempotente (`ensure_columns`). La DB ne stocke que
  la **référence** opaque, jamais le token (spec merge-writeback).
- **`registry`** : `create_project(..., credential_ref=None)` + nouvelle `set_credential_ref(conn, slug, ref)`
  (lie/délie la référence — l'affordance « token par repo » de l'onboarding écrit ici).
- **`git/internal`** : nouvelle primitive pure `credential_env(token, base=…)` — injecte le token pour un
  push GitHub HTTPS via `GIT_CONFIG_*` (`url.insteadOf`, `x-access-token`), **le temps du push seulement**,
  jamais dans un `.gitconfig` ni dans l'argv, `GIT_TERMINAL_PROMPT=0`. `InternalGit(cred_resolver=…)` résout
  la référence à l'usage ; `merge_writeback` l'injecte quand une `creds_ref` est présente (sinon push
  ambiant — compat). Le paquet git n'importe **jamais** `cockpit.secrets` (la policy vit chez l'appelant).
- **`gate/merge`** : `run_merge` lit `project['credential_ref']` et construit `InternalGit` doté du résolveur
  adossé au store actif (`build_store`, **lazy** : le store n'est bâti que si une réf est présentée ;
  **total** : secret absent/illisible → `''` → push best-effort, jamais bloquant). **0 token en DB**.

### Secret store pluggable (phase 4a — onboarding self-hosted)
- Nouveau paquet **`src/cockpit/secrets/`** : Protocol `SecretStore` (`put→ref` / `get` / `delete` / `has` /
  `list_entries`) + deux backends. La DB stocke une **référence opaque** (`credential_ref`), jamais le token ;
  le store résout à l'usage. Socle stdlib-pur (crypto/SDK importés paresseusement).
- **`EncryptedFileStore`** (défaut) : chiffrement authentifié au repos via **Fernet** (dép cœur `cryptography`),
  clé-600 + blob sous `home/secrets/`. Écritures atomiques (`O_EXCL`/`os.replace`). Invariant testé : **0
  plaintext au repos** (la valeur n'apparaît nulle part en clair), refus si blob altéré/clé absente.
- **`BwsStore`** (extra optionnel `cockpit[bws]`) : Bitwarden Secrets Manager via le **SDK officiel**
  (`bitwarden-sdk`, région configurable `BWS_API_URL`/`BWS_IDENTITY_URL`), secrets par **UUID**, cache
  process-lifetime (auth réutilisée), `client_factory` injectable. Racine = `BWS_ACCESS_TOKEN` (env ou
  fichier-600). `put`/`delete` non supportés (bring-your-own UUID) → `SecretUnsupported`.
- **`config`** : sélecteur `secret_store` (`COCKPIT_SECRET_STORE`, défaut `file`) + propriété `secrets_dir` ;
  `secrets.build_store(settings)` choisit le backend. `Settings` reste rétro-compatible (nouveau champ à défaut).

### Structure (phase repo-structure)
- Squelette du package `src/cockpit/` (src-layout, hatchling), CLI `cockpit` câblée (project/roadmap/task/
  dispatch/gate/merge/serve), imports serveur paresseux.
- **Socle fonctionnel** : `config` (résolveur générique des racines), `core/{run,ids,fs}` (exécution
  locale + slugs + accès borné), `db/{schema,store}` (schéma SQLite `SCHEMA_VERSION=1`, 4 tables).
- **Stubs documentés** pour toutes les couches (git/projects/roadmap/dispatch/gate/daemon/terminal), chacun
  pointant sa source vault + son refactor `#N` (`docs/weak-points.md`).
- `docs/` : architecture, schema-contract (SQLite + roadmap.yaml + API), weak-points (13 dettes refusées →
  refactor), multi-os (WSL-first), `specs/` (6 décisions distillées en contraintes de design).
- `.claude/` vendoré (persona `tool-builder`, skills `port-tool` + `quality-gate` adapté smoke-réponse,
  hook post-edit, templates), `.gitattributes` (eol=lf), `PORTING.md`, ce changelog.

### Portage (phase port-tools)
- Couches **git/internal**, **projects/registry**, **roadmap/model**, **roadmap/resolver** portées (SoT bare
  local, worktree flock, DAG `classify`+`eff_prio`).
- **Schéma SQLite v2** (bump `SCHEMA_VERSION=2`) : `dispatch_jobs` gagne `session_id` + métriques
  (`num_turns`/`cost_usd`/`wall_s`/`engine`) ; nouvelle table `port_reservations` (broker de ports mono-hôte).
  Migration en place idempotente (`ensure_columns`).
- Couche **dispatch** : `ports` (broker déterministe simplifié mono-hôte), `worktree` (réserve worktree+port,
  cleanup avant delete-branch), `worker` (spawn `claude -p` **local** via runner injectable, prompt sur stdin,
  gate **no-task-no-dispatch**), `jobs` (état + suivi de log **incrémental** offset/inode, normaliseur porté).
- Couche **roadmap/prompt** : synthétiseur de prompt worker (pattern `plan_prompt`, contexte in-repo, sans
  corpus vault).
- `git/internal.init_sot` **amorce** désormais `dev`+`main` (commit racine) pour qu'une feature ait une base.
- Couche **gate** (chaîne d'autorité, internal-first) : `review` (verdict Tier-1 **lié au SHA de la feature**
  + garde déterministe `evidence ⊂ diff`, état sous config clé (projet, feature)), `verify` (Tier-1.5
  feature-verified **N/A-safe** + **fail-closed**, runner node par config), `merge` (`compose_merge_decision`
  portée **verbatim** — Tier-0 non-overridable → natif N/A-safe → Tier-1 SHA-bound → Tier-1.5 conditionnel-UI
  → **GO humain** ; gate-vert-sans-go = `hold`) + `run_merge` **internal-first** : ff `feature→dev→main`
  (main-suit-dev), writeback identité injectée, **cleanup worktree AVANT `delete_branch`**, clôture DB
  (feature `merged`, tasks landées `done`). Merger une feature jamais dispatchée = outcome propre (pas de crash).
- `GitBackend` gagne les primitives `feature_sha` / `diff_names` / `diff_text` (lectures d'ancrage du gate) et
  `commit_worktree` (la forge committe le travail du worker, qui ne fait pas de git) ; `git/identity` (nouveau,
  neutre) : identité writeback déterministe `<projet>-<base>-<rôle>` (port de `worker_identity`). Le dispatch
  committe le travail worker en fin de run réussi (SHA d'ancrage pour le gate).
- Couche **daemon** (FastAPI, **DI explicite** anti god-module) : `deps` (conteneur `Deps` posé sur
  `app.state`, lu par `get_deps` — plus de `import server`), `app.build_app` (routers par domaine + handlers
  d'erreur `KeyError`→404 / `ValueError`→400, import fastapi/uvicorn **paresseux**), routers **fins**
  `routes/{projects,roadmap,dispatch,gate,terminal}` délégant aux couches portées. Dispatch en threadpool
  (le spawn `claude -p` peut bloquer). On **jette** gpu/host/proxmox/spawn/signals/qa/auth/ports-HTTP du legacy.
- Couche **terminal** : `pty.pty_bridge` **local** (PTY sur `bash -l`, `cwd=workdir` — plus de ssh `-tt`, #2) ;
  workdir borné par `core.fs.safe_path` (#4). Exposé en WebSocket `/ws/terminal/{project}`.
- Config ruff : `flake8-bugbear.extend-immutable-calls` (idiome FastAPI `Depends` en défaut d'argument).

### Modèle d'entité projet/outil (phase cockpit-productization P3)
- **Schéma SQLite v3** (bump `SCHEMA_VERSION=3`) : `projects` gagne `kind` (`project`|`tool`, CHECK,
  défaut `project`) + `owner` (nullable, compat multi-user). **Une seule table + discriminateur** (pas deux).
  Migration en place idempotente (`ensure_columns` — défaut littéral `kind='project'` sur l'existant) ;
  garde ajoutée : `ensure_columns` **saute** une table absente (ALTER sûr sur base partielle). Cf.
  `docs/schema-contract.md` §1 + migration v2→v3. Ajout **non-breaking**.
- `registry.create_project` accepte `kind`/`owner` (valide `kind∈{project,tool}` → `ValueError`/400) ; CLI
  `project create --kind {project,tool}` ; route `POST /api/projects` expose `kind`.
- **Front** : rail **2 sections** (`ProjectRail` → **Projets** / **Outils** partitionnés par `kind`) sous
  « Espace de travail » ; `ProjectSchema` + `kind`/`owner`, `CreateProjectInput.kind`. `ui_shot` seede des
  outils démo (section « Outils » VOYANTE). Feature-verified visuellement (`/`).

### Vue Git (phase cockpit-productization P2)
- **Route read-only** `GET /api/projects/{p}/git` (nouveau `routes/git`, monté dans `app.build_app`) : vue du
  SoT bare — branches, avance/retard `main` vs `dev` (le signal « main rattrape dev »), log court par réf
  protégée. Aucune mutation (le cycle git reste dans `gate/merge`). Ajout **non-breaking** (nouvelle route,
  pas de bump). Cf. `docs/schema-contract.md` §3.
- **Primitives bare-safe** dans `git/internal` : `branches` (for-each-ref → nom·sha·sujet), `log`
  (log --oneline parsé), `ahead_behind` (rev-list --left-right --count). Read-only, ni index ni working-tree.
- **Front** : onglet **Git** (`pages/GitTab` + route `git` + entrée `WorkspaceTabs`) — bannière de synchro
  dev↔main, branches teintées par réf (`gitBranchTone`), log par réf. Schémas Zod `GitView*` + `api.getGit` +
  `useGit`. Boucle visuelle : `ui_shot.py` seede un état « dev en avance sur main » (route `/…/git`).
