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

import os
from pathlib import Path
from typing import TYPE_CHECKING

from cockpit import __version__
from cockpit.config import Settings

if TYPE_CHECKING:  # imports lourds réservés au typage — jamais au runtime d'import du module
    from fastapi import FastAPI


def web_dist_dir() -> Path:
    """Emplacement du build SPA (`web/dist`). Override par `COCKPIT_WEB_DIST`, sinon relatif au repo
    (src/cockpit/daemon/app.py → parents[3] = racine). Le build n'existe pas en install pip pure — d'où
    le montage **conditionnel** (`_mount_spa`) : le daemon reste une API valable sans front."""
    override = os.environ.get("COCKPIT_WEB_DIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "web" / "dist"


def build_app(settings: Settings) -> FastAPI:
    """Construit l'app FastAPI avec DI explicite : `Deps(settings)` posé sur `app.state`, routers de domaine
    montés. Import fastapi paresseux (le module s'importe sans le serveur)."""
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from cockpit.daemon.deps import Deps
    from cockpit.daemon.routes import (
        dispatch,
        gate,
        git,
        onboarding,
        projects,
        roadmap,
        terminal,
    )

    app = FastAPI(title="cockpit", version=__version__)
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
                        git.make_git_router, onboarding.make_onboarding_router,
                        terminal.make_terminal_router):
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
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException

    dist = web_dist_dir()
    index = dist / "index.html"
    if not index.exists():
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404, detail=f"introuvable : {path}")
        file = dist / path
        if path and file.is_file():                     # fichiers racine (favicon, etc.)
            return FileResponse(file)
        return FileResponse(index)                       # sinon → SPA (deep-link rafraîchi)


def serve(settings: Settings, *, host: str, port: int) -> int:
    """Démarre uvicorn sur `build_app(settings)`. Import uvicorn paresseux."""
    import uvicorn

    uvicorn.run(build_app(settings), host=host, port=port)
    return 0
