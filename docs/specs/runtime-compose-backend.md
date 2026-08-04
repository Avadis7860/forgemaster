# spec — Runtime : backend compose + lifecycle de déploiement (P2)

> Cible : `runtime/backend.py` (`ComposeBackend` Protocol + `PodmanCompose`), `runtime/engine.py` (policy
> `deploy/stop/restart/status` + `cli_dispatch`), `runtime/paths.py` (helpers purs). Calque exact de
> `git/backend.py` (Protocol figé) × `dispatch/orchestrator.py` (couche policy) × `dispatch/worker.py` (seam
> `Runner` injecté). Consomme la table `deployments` (P1) ; le `compose.yaml` est **semé par P3**.

## Problème tranché

P1 a livré le **modèle** de déploiement (table `deployments`, 2 par branche) mais le forgemaster ne **faisait rien
tourner**. P2 lui donne son **moteur de run** : un backend compose qui build/start/stop/restart/status le service
d'un `(projet × branche)`, où **1 (projet × branche) = 1 compose-project** dont le nom `forgemaster-<slug>-<branch>`
**EST la frontière d'isolation** (conteneurs/réseaux/volumes préfixés). Deux projets ne partagent jamais un
namespace → **anti-pollution par construction** (P4 durcira secrets/env/FS par-dessus).

## Règles verrouillées

1. **Namespace = nom de compose-project.** `compose_ref = forgemaster-<slug>-<branch>` (helper pur
   `compose_project_name`), stocké dans `deployments.compose_ref`, passé en `<cmd> -p <ref>` **et** en
   `COMPOSE_PROJECT_NAME`. L'isolation est structurelle, pas un durcissement ajouté.
2. **Moteur abstrait + configurable.** `ComposeBackend` (Protocol `@runtime_checkable`) est le contrat ;
   `PodmanCompose` l'adapter par défaut (édition publique = **podman**). Le préfixe vient de
   `Settings.compose_cmd` (défaut `("podman-compose",)` — binaire STANDALONE, car Debian 12 ne package que
   podman 4.3.1, dépourvu de la sous-commande `podman compose` ≥4.4 ; env `FORGEMASTER_COMPOSE_CMD`) → basculer sur
   `docker compose` est **un réglage, pas du code**. `ps`/`logs` frappent le moteur `podman` DIRECTEMENT
   (dérivé via `compose_engine`, qui strippe le suffixe `-compose`), jamais `compose ps`.
3. **Seam `Runner` injecté** (défaut `core.run.run`, calque `dispatch/worker.py`) → les tests ne spawnent
   **jamais** un vrai conteneur ; le smoke réel les complète contre podman.
4. **Pool de ports deploy DISTINCT.** `DEPLOY_RANGE=(5250,5329)`, séparé du pool worktree `(5170,5249)` →
   jamais de collision worktree↔deploy. Réservation idempotente par `(slug, "deploy:<branch>")` : un re-deploy
   **garde le même port**. Le port publié est injecté via `env={"FORGEMASTER_PORT": …}` (⚠ `core.run.run`
   **remplace** l'env → toujours composer depuis `os.environ`).
5. **Contexte de build depuis le SoT bare, read-only.** `InternalGit.archive(sot, branch, workdir)` extrait
   l'arbre de la réf **sans muter aucune ref ni worktree** (plus propre qu'un worktree pour un build) ;
   `feature_sha` estampe `last_deploy_sha`. Le `compose.yaml` est attendu à la racine de l'arbre (contrat P3).
6. **Transitions fail-loud, jamais de faux-vert.** `deploy` : `building` → `up -d --build` → `running`
   (+ port, `url=http://127.0.0.1:<port>`, sha, compose_ref) ; un `ComposeError`/`GitOpError` pose
   **`unhealthy`** et lève `ValueError`. `status` réconcilie sur `compose ps` (read-only) ; `ps` en échec →
   `unhealthy`. Un déploiement jamais monté (`no_deploy` / pas de workdir) est un **no-op honnête**.
7. **Port gardé au `stop`** (`down` → `stopped`, port **conservé** = URL stable, re-`up` idempotent). Il n'est
   relâché qu'à la destruction du projet (**hors P2**) — choix délibéré : une URL de déploiement ne doit pas
   changer entre deux arrêts.
8. **GET idempotent, mutations en POST.** Le `GET …/deployments` reste pur-DB (deep-link goto-only sûr) ;
   `up/down/restart` sont des **POST** (`daemon/routes/deployments.py`). `ComposeError`→`ValueError`→**400**,
   projet absent→`KeyError`→**404** (handlers globaux).

## Invariants de test (encodés dans `tests/test_runtime.py`)

- `PodmanCompose` construit l'argv exact `["podman","compose","-p","forgemaster-<slug>-<branch>","up","-d",
  "--build"]` ; l'env fusionne `os.environ` + overlay ; `("docker","compose")` swap = même surface ; `not ok`
  → `ComposeError`.
- `_parse_ps` tolère tableau JSON (docker) **et** NDJSON (podman) ; vide/illisible → `[]` (jamais faux-vert).
- `deploy` : `building→running`, port **dans (5250,5329)** et **distinct** du pool worktree, `compose_ref`/
  `url`/`last_deploy_sha` écrits ; **2 projets → refs + ports distincts, zéro collision** ; re-deploy = même port ;
  échec backend → `unhealthy` + `ValueError`.
- `stop` → `stopped` en gardant le port ; no-op honnête si `no_deploy`. `restart` → `running`. Branche invalide
  → `ValueError` (400) ; projet inconnu → `KeyError` (404).
- Routes : `POST …/{branch}/up|down` pilotent l'engine (backend monkeypatché) ; 404/400 mappés.

## Prouvé live (smoke réel, podman-rootless)

Deux projets fixtures déployés **simultanément** via la vraie CLI `forgemaster deploy up` : `running` sur ports
distincts du pool deploy (5250/5251), **HTTP 200/200**, namespaces `forgemaster-alpha-dev_*` ≠ `forgemaster-beta-dev_*` ;
`down alpha` laisse **beta up** (indépendance des namespaces confirmée sur moteur réel).
