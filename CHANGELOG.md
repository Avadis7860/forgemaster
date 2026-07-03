# Changelog

Format [Keep a Changelog](https://keepachangelog.com/). Un changement de **schéma** (SQLite / roadmap.yaml
/ API HTTP — cf. `docs/schema-contract.md`) est une entrée dédiée + un bump, jamais en douce.

## [Unreleased]

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
