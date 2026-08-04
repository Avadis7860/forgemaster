# daemon — runbook (daemon HTTP = vue sur la spine : app FastAPI, injection de deps, routers minces (SPA + API))

Le daemon est une **vue FastAPI** sur la spine : il n'héberge aucune logique métier, il l'expose en HTTP.
`build_app()` construit le conteneur `Deps` **une fois** et le pose sur `app.state`, puis monte 19 routers de
domaine + la SPA. Chaque router lit ses deps par injection explicite (`Depends(get_deps)`) — jamais de
module-global mutable (correctif #1, anti god-module `import server`). Les erreurs domaine (`KeyError`→404,
`ValueError`→400) sont mappées globalement pour garder les routers fins. Import `fastapi`/`uvicorn`
**paresseux** : le module s'importe sans les deps serveur.

## build_app() — construit l'app FastAPI, injecte les deps, monte routers + SPA
`src/forgemaster/daemon/app.py:40` · appelé par serve() / les tests
DI explicite : `Deps(settings)` posé sur `app.state.deps` (l.113), puis les 19 `make_*_router()` inclus en
boucle (l.138-148). Ajoute CORS pour le dev Vite (:5173, localhost-only, pas de credentials), la sonde
`GET /health` (liveness, pas de gate), et les deux `exception_handler` globaux (`KeyError`→404,
`ValueError`→400). `_mount_spa` monté **en dernier** (l.119) pour que le catch-all ne capte que le reste. Le
`lifespan` réconcilie au boot les jobs de dispatch orphelins (`running` zombie → killed, task→todo).

## Deps / get_deps() — le conteneur d'injection explicite (immuable, sur app.state)
`src/forgemaster/daemon/deps.py:26` (`Deps`) · `:43` (`get_deps`)
`Deps` est un dataclass `frozen` qui tient `settings` et ouvre des connexions DB **à la demande**
(`open_db()`, une par requête, refermée par le router — WAL autorise la concurrence CLI↔daemon) ; il expose
aussi `secret_store()` (store de secrets résolu à l'usage). Construit une fois par `build_app`. `get_deps`
est la dépendance FastAPI qui rend le conteneur posé sur `app.state` (`request.app.state.deps`) — aucun
global. Ne tire que `starlette` (transitif de fastapi, pour typer la `Request`), jamais les couches serveur.

## serve() / _mount_spa() — lancement uvicorn + service du build SPA
`src/forgemaster/daemon/app.py:249` (`serve`) · `:175` (`_mount_spa`) · `:25` (`web_dist_dir`)
`serve()` démarre uvicorn sur `build_app(settings)` (import uvicorn paresseux). `_mount_spa()` sert le build
en statique **seulement s'il existe** : assets hashés en cache `immutable`, `index.html` en `no-cache`
(anti-stale post-déploiement), et un catch-all `GET /{path:path}` qui fallback sur `index.html` (deep-link
client-side) mais refuse `api/`/`ws/` (→ 404 JSON, jamais index à la place d'une API). `web_dist_dir()`
résout la dist : override `FORGEMASTER_WEB_DIST` → dist empaquetée dans le wheel (`forgemaster/_web_dist`, turnkey) →
layout source (`<repo>/web/dist`). Dist absente → `_mount_missing_ui_placeholder` (fail-loud, cf. Zones).

## Le pattern `make_*_router()` — 19 routers minces
`src/forgemaster/daemon/routes/*.py` · montés par build_app()
Forme commune (invariant **daemon = vue**) : une factory `make_<x>_router() -> APIRouter` qui déclare ses
endpoints, lit `Deps` par `Depends(get_deps)`, ouvre/ferme une connexion DB par requête, et **délègue à la
spine** (core/dispatch/gate/git/roadmap) — aucune logique métier dans le router. Request bodies typés en
Pydantic `BaseModel` ; erreurs remontées en `KeyError`/`ValueError` (mappées globalement) ou `HTTPException`
explicite (fail-closed, ex. 422 gate / 403 auth). Spawns longs (`claude -p`) passés en `run_in_threadpool`.

- `make_projects_router` (`routes/projects.py:38`) — registre des projets : CRUD (create/list/get/patch), délègue à `projects.registry`.
- `make_roadmap_router` (`routes/roadmap.py:32`) — roadmap : features + tasks + NEXT.
- `make_dispatch_router` (`routes/dispatch.py:21`) — dispatch : spawn worker + suivi de job (GET jobs, `WS /ws/dispatch/{job}`).
- `make_gate_router` (`routes/gate.py:43`) — gate : verdict Tier-1 review, statut composé (preview GO=false), merge sous GO humain.
- `make_git_router` (`routes/git.py:39`) — git : visibilité read-only sur le SoT bare (branches…).
- `make_codemap_router` (`routes/codemap.py:21`) — flow : flot d'exécution inter-fonctions d'une opération.
- `make_docs_router` (`routes/docs.py:23`) — docs : carte d'un projet/outil lue depuis son repo (SoT bare).
- `make_onboarding_router` (`routes/onboarding.py:28`) — onboarding self-hosted : état de config-requise au 1er lancement.
- `make_bootstrap_router` (`routes/bootstrap.py:20`) — amorçage des outils du framework (GET aperçu idempotent).
- `make_terminal_router` (`routes/terminal.py:56`) — terminal web : WebSocket → PTY local (workdir borné).
- `make_tool_router` (`routes/tool.py:19`) — outils adoptés (`kind=tool`) : mutation gatée.
- `make_deployments_router` (`routes/deployments.py:22`) — deployments : visibilité read-only des déploiements.
- `make_types_router` (`routes/types.py:15`) — registre des bundles : types de projet offerts à la création.

## Zones non détaillées
- Les corps individuels des 19 routers (forme identique — factory + endpoints + délégation spine) : voir chaque `routes/<x>.py`.
- `_mount_missing_ui_placeholder` (`app.py:228`) : dist absente → warning + page d'aide fail-loud à `/`, l'API reste valable.
- Les Pydantic request models — `ProjectCreate`/`ProjectPatch`, `ReviewBody`/`MergeBody`, `BootstrapRequest`, `CredentialLink`, `McpWire`, `InspireRequest`, `FeatureCreate`, `TaskCreate`, … : DTO locaux d'un router, validés par FastAPI → 400/422. Ce sont des **formes**, pas des mécanismes : nommés ici pour que leur silence soit déclaré, pas subi.
