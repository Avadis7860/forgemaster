# daemon — runbook (daemon HTTP = vue sur la spine : app FastAPI, injection de deps, routers minces (SPA + API))

Le daemon est une **vue FastAPI** sur la spine : il n'héberge aucune logique métier, il l'expose en HTTP.
`build_app()` construit le conteneur `Deps` **une fois** et le pose sur `app.state`, puis monte 19 routers de
domaine + la SPA. Chaque router lit ses deps par injection explicite (`Depends(get_deps)`) — jamais de
module-global mutable (correctif #1, anti god-module `import server`). Les erreurs domaine (`KeyError`→404,
`ValueError`→400) sont mappées globalement pour garder les routers fins. Import `fastapi`/`uvicorn`
**paresseux** : le module s'importe sans les deps serveur.

## build_app() — construit l'app FastAPI, injecte les deps, monte routers + SPA
`src/forgemaster/daemon/app.py:42` · appelé par serve() / les tests
DI explicite : `Deps(settings)` posé sur `app.state.deps` (l.113), puis les 19 `make_*_router()` inclus en
boucle (l.138-148). Ajoute CORS pour le dev Vite (:5173, localhost-only, pas de credentials), la sonde
`GET /health` (**readiness**, cf. § dédié), et les `exception_handler` globaux (`KeyError`→404,
`ValueError`→400, `SchemaTooNew`→503). `_mount_spa` monté **en dernier** (l.119) pour que le catch-all ne
capte que le reste. Le `lifespan` réconcilie au boot les jobs de dispatch orphelins (`running` zombie →
killed, task→todo) — **et tolère une base illisible** : sans ce `try`, le refus y remontait en « Application
startup failed » et uvicorn sortait en 3, tuant le daemon qu'on fait démarrer précisément pour qu'il dise
pourquoi il ne sert pas. Réconcilier n'a aucun sens sur une instance qui ne sert rien.

## health() — la sonde `GET /health` : readiness, pas liveness
`src/forgemaster/daemon/app.py:160` · appelé par `apply_update._wait_health` et par le front (`useHealth`, 10 s)
Rend `{status, version, ready, detail}` : **200** si l'instance peut servir, **503** sinon, `detail` portant le
motif et les gestes qui débloquent (`store.readiness`). Ce n'est pas cosmétique : `apply_update._verify_live`
tire son verdict de **cette** sonde, donc un 200 sur une instance qui rend 503 partout ailleurs ferait conclure
au succès d'une MAJ — ou d'un retour arrière — qui vient de la casser, sans déclencher le retour du retour.
`version` est rendu dans les deux cas (savoir *quel* binaire refuse fait partie du diagnostic).
Détail d'implémentation contre-intuitif : la route rend une `JSONResponse` explicite avec `response_model=None`,
et **ne prend pas** de paramètre `response: Response`. Sous `from __future__ import annotations`, les
annotations sont des chaînes que FastAPI résout dans les globals du **module** — or fastapi est importé dans le
corps de `build_app` (import paresseux), donc `Response` y devient un paramètre de requête non résolu → **422**
sur une route sans argument. Mesuré, pas déduit.

## Deps / get_deps() — le conteneur d'injection explicite (immuable, sur app.state)
`src/forgemaster/daemon/deps.py:26` (`Deps`) · `:43` (`get_deps`)
`Deps` est un dataclass `frozen` qui tient `settings` et ouvre des connexions DB **à la demande**
(`open_db()`, une par requête, refermée par le router — WAL autorise la concurrence CLI↔daemon) ; il expose
aussi `secret_store()` (store de secrets résolu à l'usage). Construit une fois par `build_app`. `get_deps`
est la dépendance FastAPI qui rend le conteneur posé sur `app.state` (`request.app.state.deps`) — aucun
global. Ne tire que `starlette` (transitif de fastapi, pour typer la `Request`), jamais les couches serveur.

## serve() / _mount_spa() — lancement uvicorn + service du build SPA
`src/forgemaster/daemon/app.py:317` (`serve`) · `:234` (`_mount_spa`) · `:26` (`web_dist_dir`)
`serve()` interroge `startup_readiness()` **avant** uvicorn, imprime le motif sur stderr si l'instance ne peut
pas servir — puis **démarre quand même**, et c'est délibéré (2026-08-06, revenant sur le refus au démarrage
posé la veille). Sortir en 1 semblait fail-closed ; mesuré, c'en était le contraire : l'unité porte
`Restart=on-failure`, donc un refus dont aucun redémarrage ne guérit devenait une **boucle**, et le message
qui nomme les gestes qui débloquent ne vivait plus que dans le journal — hors d'atteinte de qui n'ouvre pas de
terminal, ce que ce produit promet. Un daemon qui démarre et **dit** pourquoi est joignable ; un daemon absent
ne l'est pas. Le garde lui-même n'a pas bougé : la base n'est jamais ouverte au-delà du schéma connu.
`startup_readiness()` est une fonction **nommée** et non un `if` en ligne : c'est la couture par laquelle les
tests l'interrogent sans lancer un serveur. Les trois chemins qui ouvrent la base sont donc couverts : les
verbes (frontière `cli.main`), les routes (handler `SchemaTooNew` → **503**, jamais 500 — une connexion PAR
REQUÊTE), et le lifespan (§ `build_app`). `_mount_spa()` sert le build
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

- `make_projects_router` (`routes/projects.py:39`) — registre des projets : CRUD (create/list/get/patch), délègue à `projects.registry`.
- `make_roadmap_router` (`routes/roadmap.py:32`) — roadmap : features + tasks + NEXT.
- `make_dispatch_router` (`routes/dispatch.py:21`) — dispatch : spawn worker + suivi de job (GET jobs, `WS /ws/dispatch/{job}`).
- `make_gate_router` (`routes/gate.py:43`) — gate : verdict Tier-1 review, statut composé (preview GO=false), merge sous GO humain.
- `make_git_router` (`routes/git.py:39`) — git : visibilité read-only sur le SoT bare (branches…).
- `make_codemap_router` (`routes/codemap.py:21`) — flow : flot d'exécution inter-fonctions d'une opération.
- `make_docs_router` (`routes/docs.py:24`) — docs : carte d'un projet/outil lue depuis son repo (SoT bare).
- `make_onboarding_router` (`routes/onboarding.py:28`) — onboarding self-hosted : état de config-requise au 1er lancement.
- `make_bootstrap_router` (`routes/bootstrap.py:21`) — amorçage des outils du framework (GET aperçu idempotent).
- `make_terminal_router` (`routes/terminal.py:56`) — terminal web : WebSocket → PTY local (workdir borné).
- `make_tool_router` (`routes/tool.py:19`) — outils adoptés (`kind=tool`) : mutation gatée.
- `make_deployments_router` (`routes/deployments.py:22`) — deployments : visibilité read-only des déploiements.
- `make_types_router` (`routes/types.py:15`) — registre des bundles : types de projet offerts à la création.

## Zones non détaillées
- Les corps individuels des 19 routers (forme identique — factory + endpoints + délégation spine) : voir chaque `routes/<x>.py`.
- `_mount_missing_ui_placeholder` (`app.py:287`) : dist absente → warning + page d'aide fail-loud à `/`, l'API reste valable.
- Les Pydantic request models — `ProjectCreate`/`ProjectPatch`, `ReviewBody`/`MergeBody`, `BootstrapRequest`, `CredentialLink`, `McpWire`, `InspireRequest`, `FeatureCreate`, `TaskCreate`, … : DTO locaux d'un router, validés par FastAPI → 400/422. Ce sont des **formes**, pas des mécanismes : nommés ici pour que leur silence soit déclaré, pas subi.
