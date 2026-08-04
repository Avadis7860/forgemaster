"""routes/frontmap — router de domaine « design-system » : le DS indexé d'un projet (tokens + primitives +
routes), rendu par le panneau Frontmap du forgemaster (onglet Ops), en parité avec ce que le worker interroge
via `frontmap` au contrat (`bundles/base/CLAUDE.md`).

Read-only, idempotent : l'index est bâti au premier accès et **caché par (SHA, version)** (le GET ne mute rien
de durable côté SoT ; matérialiser un arbre immuable dans un cache dérivé n'est pas un effet observable) → le
runner goto-only de la boucle visuelle l'atteint sans risque. Consomme front-map en boîte-noire
(`forgemaster.frontmap.catalog`). Projet absent → 404 (KeyError global) ; index/build indisponible → 422.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from forgemaster.daemon.deps import Deps, get_deps
from forgemaster.frontmap import catalog as catalog_svc
from forgemaster.frontmap.index import FrontmapError
from forgemaster.projects.registry import get_project


def make_frontmap_router() -> APIRouter:
    router = APIRouter(tags=["frontmap"])

    def _sot(deps: Deps, project: str) -> Path:
        """Résout le SoT bare d'un projet (KeyError projet absent → 404 handler global)."""
        conn = deps.open_db()
        try:
            return Path(get_project(conn, project)["sot_path"])
        finally:
            conn.close()

    def _serve(verb: str, project: str, ref: str, deps: Deps) -> dict:
        """Bâtit l'index au 1ᵉʳ accès (cache SHA+version) et relaie le JSON du verbe catalogue. Projet absent
        → 404 (handler global) ; build/index en échec → 422."""
        sot = _sot(deps, project)
        fn = getattr(catalog_svc, verb)
        try:
            return fn(deps.settings, project, sot, ref=ref)          # type: ignore[no-any-return]
        except FrontmapError as exc:
            raise HTTPException(status_code=422, detail=f"index front-map indisponible : {exc}") from exc

    @router.get("/api/projects/{project}/frontmap/tokens")
    def frontmap_tokens(project: str, ref: str = "dev", deps: Deps = Depends(get_deps)) -> dict:
        """Design tokens indexés du projet — alimente la section Tokens du panneau Frontmap."""
        return _serve("tokens", project, ref, deps)

    @router.get("/api/projects/{project}/frontmap/primitives")
    def frontmap_primitives(project: str, ref: str = "dev", deps: Deps = Depends(get_deps)) -> dict:
        """Catalogue de primitives (composants) du projet — section Primitives du panneau Frontmap."""
        return _serve("primitives", project, ref, deps)

    @router.get("/api/projects/{project}/frontmap/routes")
    def frontmap_routes(project: str, ref: str = "dev", deps: Deps = Depends(get_deps)) -> dict:
        """Arbre des routes du front du projet — section Routes du panneau Frontmap."""
        return _serve("routes", project, ref, deps)

    return router
