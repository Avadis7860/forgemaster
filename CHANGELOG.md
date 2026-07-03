# Changelog

Format [Keep a Changelog](https://keepachangelog.com/). Un changement de **schéma** (SQLite / roadmap.yaml
/ API HTTP — cf. `docs/schema-contract.md`) est une entrée dédiée + un bump, jamais en douce.

## [Unreleased]

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
