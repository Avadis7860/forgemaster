"""routes/projects — router de domaine « registre des projets » (create/list/get). Router **fin** : il
délègue à `projects.registry` (couche portée), lit `Deps` par injection explicite, ne connaît aucun global.
Éclatement du monolithe orchestrator (#3) ; plus de `import server` (#1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cockpit.daemon.deps import Deps, get_deps
from cockpit.projects import registry


class ProjectCreate(BaseModel):
    slug: str
    name: str | None = None
    mirror_remote: str | None = None


def make_projects_router() -> APIRouter:
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    @router.get("")
    def list_projects(deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            return {"projects": registry.list_projects(conn)}
        finally:
            conn.close()

    @router.post("", status_code=201)
    def create_project(body: ProjectCreate, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            return registry.create_project(conn, deps.settings, slug=body.slug, name=body.name,
                                           mirror_remote=body.mirror_remote)
        finally:
            conn.close()

    @router.get("/{slug}")
    def get_project(slug: str, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            return registry.get_project(conn, slug)     # KeyError → 404 (handler global)
        finally:
            conn.close()

    return router
