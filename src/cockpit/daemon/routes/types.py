"""routes/types — router de domaine « registre des bundles » : les types de projet OFFERTS à la création.

Read-only, idempotent : lecture du filesystem vendoré (`provision.list_valid_types`) filtrée par validation
fail-closed → aucun effet observable, le runner goto-only de la boucle visuelle l'atteint sans risque. Source
UNIQUE des types offerts (mêmes types que le durcissement des `choices` CLI). Alimente le dropdown de
`NewProjectForm` ; réutilisable par la future surface de gestion des bundles (P5).
"""
from __future__ import annotations

from fastapi import APIRouter

from cockpit.provision import list_valid_types


def make_types_router() -> APIRouter:
    router = APIRouter(prefix="/api/types", tags=["types"])

    @router.get("")
    def list_types() -> dict:
        """Les types de projet **valides** + leurs métadonnées (version, facets, default_facet). Filtré
        fail-closed : un bundle cassé n'apparaît pas (on n'offre pas ce qu'on ne saurait pas semer). GET
        idempotent (lecture de fichiers vendorés — goto-safe)."""
        return {"types": list_valid_types()}

    return router
