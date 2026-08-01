# projects — runbook (registre de projets (SoT-local, provenance, mirror remote, cred ref) + déploiements par projet)

Le registre `projects` est le SoT des entités pilotées par la forge : chaque projet porte une identité durable (`slug`, `sot_path` bare local, `mirror_remote` best-effort, `backend`, `kind`, `project_type`). Le SoT-local (roadmap/décisions du projet) vit dans un dépôt bare co-localisé sous `settings.projects_root` ; le miroir GitHub n'est qu'une copie best-effort. Le credential se stocke **par référence** (`credential_ref` opaque) — jamais le token en clair : la valeur vit dans le store de secrets, résolue à l'usage. Les déploiements (`deployments.py`) attachent à chaque projet 2 lignes de run par branche (`main` prod, `dev` preview).

## create_project() — crée la row + initialise le SoT bare (seed ou adoption)
`src/cockpit/projects/registry.py:65` · appelé par `cli_dispatch` (action `create`) et le router `services/aggregator/routers/projects.py`
Deux chemins mutuellement exclusifs. **SEED** (`source_url=None`) : le SoT est semé du bundle `base ⊕ overlay(project_type)` via `git.init_sot`, l'INSERT précédant le seed (compat historique). **ADOPTION** (`source_url` fourni) : le SoT est un **clone bare** du repo distant, fait **avant** l'INSERT pour ne pas laisser de row orpheline si le clone échoue ; auth optionnelle via `credential_ref` + `cred_resolver` (token résolu à l'usage, injecté transitoirement par `credential_env`). Valide slug + `kind` + `project_type` (fail-closed) avant tout effet ; sème un tampon de provenance et la roadmap de lancement en chemin SEED ; appelle `ensure_deployments` en fin. Lève `ValueError` (slug/kind invalide, doublon, clone échoué). Une nuance anti-cap-silencieux sur le refus de type : un type « inconnu » peut être un type ajouté **après** le build de ce cockpit — quand le miroir SoT local le connaît, le `BundleError` est enrichi (`build_provenance.stale_type_hint` nomme le type, le retard, et dit de réinjecter) au lieu du sec « type inconnu ». L'ordre fail-closed est intact : on relève toujours avant le moindre effet.

## sot_path_for() — chemin déterministe du SoT bare
`src/cockpit/projects/registry.py:56` · appelé par `create_project`
Renvoie `<projects_root>/<slug>/sot.git`. Déterministe, entièrement sous config (`settings.projects_root`) — aucun chemin d'hôte en dur (Refactor #4).

## set_credential_ref() — lie/délie la référence de token (jamais le secret)
`src/cockpit/projects/registry.py:160` · appelé par le router (affordance « token par repo » de l'onboarding)
`UPDATE` de la seule colonne `credential_ref` (opaque) ; `None` délie. La DB ne porte que la **référence** — la valeur du token vit dans le store de secrets. Lève `KeyError` si le projet n'existe pas ; retourne le projet relu via `get_project`.

## set_mirror_remote() — configure/retire le miroir GitHub + matérialise dans git
`src/cockpit/projects/registry.py:171` · appelé par le router (affordance « rendre GitHub-backed »)
Normalise (`""`→`None`), `UPDATE` la colonne `mirror_remote`, puis matérialise dans git : `git.set_remote(sot, "mirror", …)` si configuré, sinon `git.remove_remote`. Un miroir configuré rend un token de push *requis* (best-effort : le SoT local reste la vérité). Lève `KeyError` si absent ; retourne le projet relu.

## list_projects() — tous les projets triés par slug
`src/cockpit/projects/registry.py:210` · appelé par `cli_dispatch` (action `list`)
`SELECT *` trié par `slug`, mappé en `list[dict]`. Lecture pure.

## get_project() — un projet par slug
`src/cockpit/projects/registry.py:215` · appelé par `set_credential_ref`, `set_mirror_remote`, `cli_dispatch` (action `get`)
`SELECT` par slug ou `KeyError` s'il n'existe pas. Point de relecture partagé après les mutations.

## ensure_deployments() — garantit (idempotent) les 2 lignes main/dev en no_deploy
`src/cockpit/projects/deployments.py:25` · appelé par `create_project` (à la création) et `list_deployments` (à la lecture)
`INSERT OR IGNORE` sur la contrainte `UNIQUE(project_id, branch)` pour chaque branche de `_BRANCHES` — ré-appel sans effet ni duplication. Semé à la création ET rejoué à la lecture pour couvrir les projets d'avant v7 (table `deployments` neuve, partie vide).

## list_deployments() — les 2 déploiements, main puis dev
`src/cockpit/projects/deployments.py:40` · appelé par le router deployments / P5 observabilité
`ensure_deployments` d'abord (robustesse pré-v7), puis `SELECT … ORDER BY branch DESC` — `main` > `dev` en lexical place prod avant preview (ordre stable documenté).

## get_deployment() — un déploiement par (projet, branche)
`src/cockpit/projects/deployments.py:50` · appelé par `set_deployment` (relecture) et les consommateurs P2/P5
`SELECT` par `(project_id, branch)` ou `KeyError` (`"<project_id>/<branch>"`) s'il n'existe pas.

## set_deployment() — upsert partiel de l'état d'un déploiement
`src/cockpit/projects/deployments.py:59` · appelé par P2 (deploy) et P5 (observabilité)
Upsert **partiel** : seuls les champs non-`None` (`status`, `port`, `url`, `last_deploy_sha`, `compose_ref`) sont écrits (`None` = inchangé), `updated_at` toujours bumpé. Colonnes littérales dans le SQL (jamais d'input utilisateur). Lève `ValueError` si `branch` hors `{main,dev}`, `KeyError` si le déploiement n'existe pas ; retourne la row relue via `get_deployment`.

## Zones non détaillées
- `_now()` (`registry.py:32`, `deployments.py:21`) — horodatage ISO-8601 UTC ; dans `create_project`, calculé une fois et partagé par la row DB et le tampon de provenance (accord).
- `_render_provenance()` (`registry.py:36`) — rend le TOML `.cockpit/provenance.toml` (`bundle@version` + `created_at`) semé dans le SoT à l'instanciation (socle SoT-and-derive) ; chemin SEED uniquement.
- `_slug_exists()` (`registry.py:61`) — pré-check d'unicité avant un clone d'adoption (évite un clone gâché avant le heurt d'unicité).
- `record_interview_session()` (`registry.py`) — trace la session d'interview d'un projet (la task `interactive` menée au terminal, cf. `dispatch-worker`), pour qu'un cadrage mené à la main laisse une trace comme un run headless.
- `_seed_launch_roadmap()` (`registry.py:146`) — sème la roadmap de lancement du bundle (chemin SEED), **fail-soft** (warning, jamais bloquant) ; import paresseux de `roadmap.seed` pour casser le cycle `registry ↔ roadmap.model`.
- `cli_dispatch()` (`registry.py:223`) — route `cockpit project <create|list|get>`, ouvre/ferme la connexion, mappe `ValueError`/`KeyError` en code de sortie 1.
