"""routes/codemap — router de domaine « flow » : le flot d'exécution inter-fonctions d'une opération d'un
projet (routes API + verbes CLI), rendu par l'onglet Flow du cockpit.

Read-only, idempotent : l'index est bâti au premier accès et **caché par SHA** (le GET ne mute rien de
durable côté SoT ; matérialiser un arbre immuable dans un cache dérivé n'est pas un effet observable) → le
runner goto-only de la boucle visuelle l'atteint sans risque. Consomme code-map en boîte-noire
(`cockpit.codemap.flow`). Projet absent → 404 (KeyError global) ; index/flot indisponible → 422.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from cockpit.codemap import flow as flow_svc
from cockpit.codemap.index import CodemapError
from cockpit.daemon.deps import Deps, get_deps
from cockpit.projects.registry import get_project


def make_codemap_router() -> APIRouter:
    router = APIRouter(tags=["flow"])

    def _sot(deps: Deps, project: str) -> Path:
        """Résout le SoT bare d'un projet (KeyError projet absent → 404 handler global)."""
        conn = deps.open_db()
        try:
            return Path(get_project(conn, project)["sot_path"])
        finally:
            conn.close()

    @router.get("/api/projects/{project}/flow/operations")
    def flow_operations(project: str, ref: str = "dev", deps: Deps = Depends(get_deps)) -> dict:
        """Opérations découvertes (entry points : routes API + verbes CLI) — alimente le sélecteur de
        l'onglet Flow. Bâtit l'index au 1ᵉʳ accès (cache SHA). Projet absent → 404 ; build/index en échec
        → 422."""
        sot = _sot(deps, project)
        try:
            return flow_svc.list_operations(deps.settings, project, sot, ref=ref)
        except CodemapError as exc:
            raise HTTPException(status_code=422, detail=f"index code-map indisponible : {exc}") from exc

    @router.get("/api/projects/{project}/flow")
    def flow_graph(
        project: str, operation: str, ref: str = "dev", depth: int = flow_svc.DEFAULT_DEPTH,
        deps: Deps = Depends(get_deps),
    ) -> dict:
        """Sous-graphe de flot d'une opération (`{ok, operation, entry, nodes[], edges[], stats}`). `ok:false`
        (opération introuvable/ambiguë) est renvoyé tel quel en 200 — l'UI l'affiche comme « rien à montrer »,
        ce n'est pas une erreur serveur. Projet absent → 404 ; build/index en échec → 422."""
        sot = _sot(deps, project)
        try:
            return flow_svc.flow(deps.settings, project, sot, operation=operation, ref=ref, depth=depth)
        except CodemapError as exc:
            raise HTTPException(status_code=422, detail=f"flot indisponible : {exc}") from exc

    return router
