# runtime — runbook (déploiement conteneur (podman-compose) : cycle de vie d'un déploiement, backend injectable)

Déploiement **local** d'un projet par `podman compose` (ou `docker compose`, simple réglage `Settings.compose_cmd`). L'`engine` est la couche **policy** (git + ports + deployments + backend) ; le `backend` est la couche **transport** (argv compose). Le compose-backend est **injectable** (`ComposeBackend` protocol → `PodmanCompose` par défaut) et son runner subprocess l'est aussi → deploys **testables sans runtime conteneur réel**. Frontière d'isolation = le compose-project `cockpit-<slug>-<branch>`.

## engine.deploy() — building → up → running (+ port, url, sha)
`src/cockpit/runtime/engine.py:59` · appelé par `cli_dispatch` (`up`) et la route deploy
Séquence : `_resolve` (projet + branche) → `set_deployment(building)` → `git.archive` extrait l'arbre de la réf dans `deploy_dir_for` (snapshot read-only du SoT bare, aucun ref muté) + `feature_sha` → pré-vol honnête (aucun `compose.yaml` parmi `_COMPOSE_FILENAMES` → `ValueError`, type non hébergeable) → `ports.reserve` sur `DEPLOY_RANGE` (5250-5329, pool distinct du worktree) → `backend.up` avec l'env overlay `{COCKPIT_PORT, COMPOSE_PROJECT_NAME}` → `set_deployment(running, url=http://127.0.0.1:<port>)`. Tout échec (git / compose) pose `unhealthy` et lève `ValueError`. Idempotent sur le port.

## engine.stop() — down → stopped, port conservé
`src/cockpit/runtime/engine.py:119` · appelé par `cli_dispatch` (`down`)
`backend.down` (conteneurs + réseau retirés, namespace nettoyé) → `stopped`. Le **port reste réservé** (URL stable, re-`up` idempotent), relâché seulement à la destruction du projet. Vide honnête : `no_deploy` ou workdir absent → renvoie le `dep` tel quel (no-op). Env fourni par `_compose_env` (COCKPIT_PORT présent pour le re-parse).

## engine.restart() — restart → running
`src/cockpit/runtime/engine.py:140` · appelé par `cli_dispatch` (`restart`)
`backend.restart` sur un déploiement **déjà monté** (après un `down`, il faut ré-`up`, pas `restart`). Échec → `unhealthy` + `ValueError`. Le port réservé (lu en DB) alimente `COCKPIT_PORT` via `_compose_env` pour le re-parse du compose.

## engine.status() — réconcilie DB ↔ live (ps)
`src/cockpit/runtime/engine.py:158` · appelé par `cli_dispatch` (`status`) et la route status
Read-only, idempotent : `backend.ps` → `running` si au moins un conteneur `is_running`, sinon `stopped` ; `ps` en échec → `unhealthy`. Jamais un faux-vert : `no_deploy` / workdir absent → rendu tel quel.

## engine.logs() — tail borné, vide honnête
`src/cockpit/runtime/engine.py:183` · appelé par la route logs
`backend.logs(tail=n)` avec `n` **clampé** dans `[1, 1000]` (`_LOGS_TAIL_MAX`). Vide honnête : `no_deploy` / workdir absent → `{"lines": []}`. Échec compose → `ValueError` (→ 400). Renvoie `{"lines": [...]}`.

## engine.cli_dispatch() — route `cockpit deploy <action>`
`src/cockpit/runtime/engine.py:274` · appelé par le parseur CLI cockpit
Route `up|down|restart|status <slug> <branch>` via la table `_ACTIONS`. Corps canonique : `open_db` → dispatch sur `args.action` → `(ValueError, KeyError)` → `erreur`/`1` → `finally close`. Imprime `<slug>/<branch> : <status>` (+ `→ url` si présent).

## backend.ComposeBackend — le seam injectable (Protocol)
`src/cockpit/runtime/backend.py:48` · implémenté par `PodmanCompose`, injecté dans chaque verbe engine
Contrat `@runtime_checkable` du moteur de run : `up` / `down` / `restart` / `ps` / `logs`, chacun opérant sur UN compose-project (`project_name`, le namespace) dans un `workdir` (qui porte le `compose.yaml`), levant `ComposeError` sur échec dur. C'est ce seam qui rend deploy testable sans conteneur : un fake honorant le Protocol se substitue au `PodmanCompose` par défaut via l'argument `backend=`.

## backend.PodmanCompose — l'adapter concret sur la CLI compose
`src/cockpit/runtime/backend.py:120` · défaut construit par chaque verbe engine (`backend or PodmanCompose(...)`)
Construit l'argv `<cmd> -p <name> <sous-commande>` exécuté dans `workdir` via son **runner injecté** (`runner=` ; défaut `_default_runner` → `core.run.run`). Env scellé par `_base_env` (allowlist `_COMPOSE_ENV_ALLOW` ⊕ overlay — aucun secret du daemon ne fuit, P4). `up` = `up -d --build` ; `down`/`restart` directs. Note : `ps` et `logs` interrogent le **moteur directement** (`<engine> ps/logs` filtré par label `com.docker.compose.project`), PAS `compose ps/logs`, car podman-compose 1.0.6 ne gère ni `--format json` ni des logs stdout fiables.

## backend.runtime_available() — sonde binaire (pure)
`src/cockpit/runtime/backend.py:78` · appelé par `_default_runner` (preflight) + `doctor`
`True` si `cmd[0]` (podman/docker) résout sur le PATH (`shutil.which`). Pure, sans sous-process. Sonde le PATH passé (celui de l'env scellé, pas `os.environ`).

## backend.is_running() — état live d'un conteneur
`src/cockpit/runtime/backend.py:219` · appelé par `engine.status` sur chaque row de `ps`
`True` si `State == running` (docker) ou `Status` commençant par `up`/`running` (podman). Tolère les deux vocabulaires de moteur.

## backend.ComposeError — échec dur compose
`src/cockpit/runtime/backend.py:42` · levé par `PodmanCompose._checked` / `ps` / `logs`, capté par l'engine
`RuntimeError` portant le stderr tronqué. Les verbes engine le convertissent en `ValueError` (→ 400 route / `erreur` CLI) — calque de `GitOpError`, jamais un 500 opaque.

## paths.compose_project_name() — la frontière d'isolation
`src/cockpit/runtime/paths.py:11` · appelé par chaque verbe engine (`name = ...`)
`cockpit-<slug>-<branch>`. Passé en `compose -p <name>`, il préfixe conteneurs/réseaux/volumes → deux projets ou deux branches ne partagent jamais un namespace. Pur.

## paths.deploy_dir_for() — le workdir de build/run
`src/cockpit/runtime/paths.py:18` · appelé par chaque verbe engine (`workdir = ...`)
`<projects_root>/<slug>/deploy/<branch>/` : l'arbre de la réf y est extrait (`git archive`), `compose.yaml` attendu à la racine (semé par P3). Voisin du SoT bare `<slug>/sot.git`, jamais mélangé. Pur.

## Zones non détaillées
- `deploy_preview` / `teardown_preview` (`engine.py`) — le couple de la **preview** d'une branche (déploiement éphémère + démontage), distinct du déploiement nommé : même moteur, cycle de vie court.
- `compose_engine` / `compose_provider_available` (`backend.py`) — quel moteur compose est en place et est-il joignable ; c'est ce qui permet à un diagnostic de dire « podman absent » plutôt que de laisser échouer un `up`.
- `_deploy_purpose` (39) — clé de réservation de port `deploy:<branch>`, distincte de `worktree:<feature>`.
- `_resolve` (42) — résout le projet + valide la branche (fail-loud avant tout effet) ; `KeyError` → 404.
- `_compose_env` (49) — overlay `{COMPOSE_PROJECT_NAME, COCKPIT_PORT}` pour down/restart/logs (placeholder `0` si port manquant — seule la présence compte au re-parse).
- `_default_runner` (84) — runner par défaut : preflight `runtime_available` (→ `ComposeError` actionnable si binaire absent) puis `core.run.run`.
- `_parse_ps` (170) — parse `ps --format json` tolérant tableau JSON (docker) ou NDJSON (podman) ; illisible → `[]` (jamais un faux-vert).
- Helpers `PodmanCompose._base_env` (105), `_compose` (112), `_checked` (121) — construction argv + env scellé + garde `r.ok`.
- Constantes : `DEPLOY_RANGE`, `_COMPOSE_FILENAMES`, `_LOGS_TAIL_MAX`, `_ACTIONS` (engine) ; `DEFAULT_COMPOSE_CMD`, `COMPOSE_TIMEOUT`, `_COMPOSE_ENV_ALLOW` (backend).
