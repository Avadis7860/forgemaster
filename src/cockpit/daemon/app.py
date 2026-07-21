"""app — construction et lancement du daemon. **Import de fastapi/uvicorn PARESSEUX** (dans le corps des
fonctions) : le module s'importe sans les deps serveur (prouvé par test_skeleton), on ne les tire qu'au
`build_app`/`serve`. `build_app` reçoit `settings`, construit le conteneur `Deps` **une fois** et le pose
sur `app.state.deps` — jamais un module-global mutable (correctif #1, anti god-module `import server`).

Routers découpés par domaine (correctif #3, fin du monolithe 1650-LOC) : projects / roadmap / dispatch /
gate + terminal WS. On **jette** gpu/host/proxmox/spawn/signals/qa/auth/ports-HTTP du legacy (hors périmètre
d'une forge locale). Les erreurs domaine (`KeyError`→404, `ValueError`→400) sont mappées globalement pour
garder les routers fins.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from cockpit import __version__
from cockpit.config import Settings

if TYPE_CHECKING:  # imports lourds réservés au typage — jamais au runtime d'import du module
    from fastapi import FastAPI


def web_dist_dir() -> Path:
    """Emplacement du build SPA, dans l'ordre : override `COCKPIT_WEB_DIST` → dist **empaquetée** dans le
    paquet (`cockpit/_web_dist`, livrée par le wheel — turnkey, aucun Node requis) → layout **source**
    (`<repo>/web/dist`, dev/éditable, buildé par `cockpit setup`). Retourne le 1er chemin dont `index.html`
    existe ; à défaut, le chemin source (référence citée par le placeholder fail-loud de `_mount_spa`)."""
    override = os.environ.get("COCKPIT_WEB_DIST")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    packaged = here.parents[1] / "_web_dist"             # cockpit/_web_dist (livré par le wheel)
    if (packaged / "index.html").is_file():
        return packaged
    return here.parents[3] / "web" / "dist"              # layout source (repo)


def build_app(settings: Settings) -> FastAPI:
    """Construit l'app FastAPI avec DI explicite : `Deps(settings)` posé sur `app.state`, routers de domaine
    montés. Import fastapi paresseux (le module s'importe sans le serveur). Le `lifespan` réconcilie au boot
    les jobs de dispatch orphelins (worker mort/daemon redémarré ⇒ job `running` zombie)."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from cockpit.daemon.deps import Deps
    from cockpit.daemon.routes import (
        bootstrap,
        bundles,
        capital,
        codemap,
        deployments,
        dispatch,
        docs,
        gate,
        git,
        onboarding,
        projects,
        roadmap,
        templates,
        terminal,
        tool,
        types,
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        # Réconciliation au démarrage : le dispatch est synchrone in-process → aucun thread de worker ne
        # survit un restart, donc tout `dispatch_jobs.status='running'` observé ici est orphelin par
        # construction (worker tué, daemon redémarré en plein run). On le finalise (killed) + task→todo.
        import asyncio
        import contextlib

        from cockpit.db import store
        from cockpit.dispatch import reconcile
        from cockpit.terminal.registry import PtySessionRegistry, run_reaper

        conn = store.open_db(settings)
        try:
            orphans = reconcile.reconcile_orphans(conn)
            if orphans:
                logging.getLogger("cockpit").warning(
                    "réconcilié %d job(s) de dispatch orphelin(s) au boot (running→killed, task→todo) : %s",
                    len(orphans), orphans)
        finally:
            conn.close()

        # Registre des sessions PTY détachables + reaper de sessions détachées (feature « le terminal survit à
        # la déconnexion WS »). Le registre vit sur `app.state` (slot dédié, pas un god-module mutable) ; le
        # reaper est une tâche de fond annulée au shutdown.
        _app.state.terminals = PtySessionRegistry()
        reaper = asyncio.create_task(run_reaper(_app.state.terminals))
        try:
            yield
        finally:
            # Moitié shutdown (absente jusqu'ici) : couper le reaper puis tuer toute session PTY vivante ou
            # détachée — pas de shell orphelin ni de PTY fuité quand le daemon s'arrête.
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper
            _app.state.terminals.close_all()

    app = FastAPI(title="cockpit", version=__version__, lifespan=_lifespan)
    app.state.deps = Deps(settings)                      # conteneur DI unique, lu par get_deps

    # CORS pour le dev Vite (:5173 → daemon :8700). Outil strictement local : origines localhost only,
    # pas de credentials (aucune auth cookie). En prod, le front est servi same-origin (StaticFiles).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:                                # liveness self (sonde) — pas de gate
        return {"status": "ok", "version": __version__}

    for make_router in (projects.make_projects_router, roadmap.make_roadmap_router,
                        dispatch.make_dispatch_router, gate.make_gate_router,
                        git.make_git_router, codemap.make_codemap_router,
                        docs.make_docs_router, onboarding.make_onboarding_router,
                        bootstrap.make_bootstrap_router, terminal.make_terminal_router,
                        tool.make_tool_router, deployments.make_deployments_router,
                        types.make_types_router, bundles.make_bundles_router,
                        capital.make_capital_router, templates.make_templates_router):
        app.include_router(make_router())

    @app.exception_handler(KeyError)
    async def _not_found(_request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"introuvable : {exc}"})

    @app.exception_handler(ValueError)
    async def _bad_request(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    _mount_spa(app)                                      # dernier : le fallback SPA ne capte que le reste
    return app


def _mount_spa(app: FastAPI) -> None:
    """Sert le build SPA en statique + **fallback index.html** (routing client-side) — SEULEMENT si le build
    existe. Monté en dernier : `/api`, `/ws`, `/health` (routes déjà déclarées) gagnent ; le catch-all GET ne
    capte que le reste et refuse `api/`/`ws/` (→ 404 JSON, jamais index.html à la place d'une API)."""
    from typing import Any

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException
    from starlette.responses import Response

    dist = web_dist_dir()
    index = dist / "index.html"
    if not index.exists():
        _mount_missing_ui_placeholder(app, dist)         # fail-loud : jamais un 404 muet « API sans UI »
        return

    # Politique de cache SPA (évite le « j'ai redéployé mais le navigateur montre l'ancien ») : les assets
    # sont HASHÉS (content-addressed) → cache long `immutable` (un rebuild change l'URL, jamais de stale) ;
    # `index.html`, lui, N'est pas hashé et pointe les assets courants → `no-cache` (toujours revalidé), sinon
    # le navigateur le garde en cache heuristique et sert une vieille app après un déploiement.
    class _ImmutableAssets(StaticFiles):
        async def get_response(self, path: str, scope: Any) -> Response:
            resp = await super().get_response(path, scope)
            resp.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
            return resp

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", _ImmutableAssets(directory=str(assets)), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404, detail=f"introuvable : {path}")
        file = dist / path
        if path and file.is_file():                     # fichiers racine (favicon, etc.)
            return FileResponse(file)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})  # SPA (deep-link rafraîchi)


_MISSING_UI_HTML = (
    "<!doctype html><meta charset=utf-8><title>cockpit — UI non buildée</title>"
    "<style>body{font:16px/1.6 system-ui,sans-serif;margin:6rem auto;max-width:34rem;padding:0 1.5rem;"
    "color:#111}code{background:#f4f4f5;padding:.15em .4em;border-radius:4px}h1{font-size:1.4rem}</style>"
    "<h1>cockpit — l'UI n'est pas encore buildée</h1>"
    "<p>Le daemon tourne (l'API répond sur <code>/api</code> et <code>/health</code>), mais l'interface web "
    "n'a pas été construite.</p>"
    "<p><b>Depuis les sources :</b> lance <code>cockpit setup</code> puis recharge cette page.</p>"
    "<p><b>Sinon :</b> installe le wheel packagé — l'UI y est incluse, aucun Node requis.</p>"
)


def _mount_missing_ui_placeholder(app: FastAPI, dist: Path) -> None:
    """Dist absente : au lieu d'un 404 muet (API sans UI ni explication), on LOG un warning clair et on sert
    une page d'aide à `/` (et tout non-`api`/`ws`). L'API reste pleinement valable — c'est un fail-loud, pas
    une dégradation silencieuse."""
    import logging

    from fastapi.responses import HTMLResponse
    from starlette.exceptions import HTTPException

    logging.getLogger("cockpit").warning(
        "UI non buildée (dist absente : %s) — API-only. Build : `cockpit setup` (from-clone) "
        "ou installe le wheel packagé (UI incluse, aucun Node).", dist,
    )

    @app.get("/{path:path}", include_in_schema=False)
    async def _no_ui(path: str) -> HTMLResponse:
        if path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404, detail=f"introuvable : {path}")
        return HTMLResponse(_MISSING_UI_HTML)


def serve(settings: Settings, *, host: str, port: int) -> int:
    """Démarre uvicorn sur `build_app(settings)`. Import uvicorn paresseux."""
    import uvicorn

    uvicorn.run(build_app(settings), host=host, port=port)
    return 0
