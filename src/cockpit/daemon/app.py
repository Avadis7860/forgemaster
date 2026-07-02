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

from typing import TYPE_CHECKING

from cockpit import __version__
from cockpit.config import Settings

if TYPE_CHECKING:  # imports lourds réservés au typage — jamais au runtime d'import du module
    from fastapi import FastAPI


def build_app(settings: Settings) -> FastAPI:
    """Construit l'app FastAPI avec DI explicite : `Deps(settings)` posé sur `app.state`, routers de domaine
    montés. Import fastapi paresseux (le module s'importe sans le serveur)."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    from cockpit.daemon.deps import Deps
    from cockpit.daemon.routes import dispatch, gate, projects, roadmap, terminal

    app = FastAPI(title="cockpit", version=__version__)
    app.state.deps = Deps(settings)                      # conteneur DI unique, lu par get_deps

    @app.get("/health")
    def health() -> dict:                                # liveness self (sonde) — pas de gate
        return {"status": "ok", "version": __version__}

    for make_router in (projects.make_projects_router, roadmap.make_roadmap_router,
                        dispatch.make_dispatch_router, gate.make_gate_router,
                        terminal.make_terminal_router):
        app.include_router(make_router())

    @app.exception_handler(KeyError)
    async def _not_found(_request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"introuvable : {exc}"})

    @app.exception_handler(ValueError)
    async def _bad_request(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


def serve(settings: Settings, *, host: str, port: int) -> int:
    """Démarre uvicorn sur `build_app(settings)`. Import uvicorn paresseux."""
    import uvicorn

    uvicorn.run(build_app(settings), host=host, port=port)
    return 0
