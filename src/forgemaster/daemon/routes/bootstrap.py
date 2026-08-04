"""routes/bootstrap — router « amorçage des outils du framework ». `GET` = aperçu **idempotent** (ce que
l'amorçage ferait, sans effet — atteignable par le runner *goto-only* et lu par l'étape wizard) ; `POST` =
exécute l'adoption (idempotent, skip existants). Router **fin** : délègue à `forgemaster.bootstrap`, lit
`Deps`
par injection explicite. Aucun secret ne transite : le corps ne porte qu'un `shared_ref` DÉJÀ stocké."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from forgemaster import bootstrap
from forgemaster.daemon.deps import Deps, get_deps


class BootstrapRequest(BaseModel):
    # réf credential DÉJÀ dans le store (repos privés — liée au préalable via /credential ou --token-file).
    # Absente = adoption anonyme (repos publics, D7). Le token brut ne transite JAMAIS par cette route.
    shared_ref: str | None = None


def make_bootstrap_router() -> APIRouter:
    router = APIRouter(prefix="/api/bootstrap", tags=["bootstrap"])

    @router.get("")
    def bootstrap_preview(deps: Deps = Depends(get_deps)) -> dict:
        """Aperçu idempotent : `available` (manifeste présent & valide), outils + `adopted` par outil.
        Aucun effet, aucun secret. Manifeste invalide → 400 (handler global)."""
        conn = deps.open_db()
        try:
            return bootstrap.preview(conn, deps.settings)
        finally:
            conn.close()

    @router.post("")
    def run_bootstrap(body: BootstrapRequest, deps: Deps = Depends(get_deps)) -> dict:
        """Exécute l'amorçage (idempotent). Manifeste absent → no-op propre (`available:false`). Retourne
        le rapport `{created, skipped, failed, available}`."""
        conn = deps.open_db()
        try:
            manifest = bootstrap.load_manifest(deps.settings)   # ValueError (invalide) → 400
            if manifest is None:
                return {"created": [], "skipped": [], "failed": [], "available": False}
            report = bootstrap.run_bootstrap(conn, deps.settings, manifest=manifest,
                                             shared_ref=body.shared_ref)
            return {**report, "available": True}
        finally:
            conn.close()

    return router
