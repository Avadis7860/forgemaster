"""routes/deployments — router de domaine « deployments » : visibilité read-only des **2 déploiements**
(`main`/`dev`) d'un projet. **Read-only en v7 (P1)** — le lifecycle mutant (deploy/undeploy/restart) et
l'observabilité runtime arrivent en P2/P5 et brancheront ici. Sert « où en sont mes déploiements ? »."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from cockpit.daemon.deps import Deps, get_deps
from cockpit.projects.deployments import list_deployments
from cockpit.projects.registry import get_project

# surface publique exposée (champs internes gardés : id, project_id, compose_ref, created_at, updated_at)
_FIELDS = ("branch", "status", "port", "url", "last_deploy_sha")


def make_deployments_router() -> APIRouter:
    router = APIRouter(tags=["deployments"])

    @router.get("/api/projects/{project}/deployments")
    def deployments_view(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Les 2 déploiements (`main`/`dev`) d'un projet : branche, état de run, port, url, sha du dernier
        deploy. Idempotent (le runner de boucle visuelle *goto-only* l'atteint sans risque). **Vide honnête**
        — un projet jamais déployé rend ses 2 déploiements en `no_deploy` (jamais un faux-vert ni une liste
        vide trompeuse). Projet absent → 404 (handler `KeyError` global)."""
        conn = deps.open_db()
        try:
            proj = get_project(conn, project)          # KeyError projet absent → 404
            rows = list_deployments(conn, proj["id"])
        finally:
            conn.close()
        return {"project": project, "deployments": [{k: d[k] for k in _FIELDS} for d in rows]}

    return router
