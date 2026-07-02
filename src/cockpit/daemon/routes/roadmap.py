"""routes/roadmap — router de domaine « roadmap » (features + tasks + NEXT). Fin : délègue à
`roadmap.model` (CRUD) et `roadmap.resolver` (DAG), le graphe reste la seule autorité de séquencement."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cockpit.daemon.deps import Deps, get_deps
from cockpit.roadmap import model, resolver


class FeatureCreate(BaseModel):
    slug: str
    title: str | None = None


class TaskCreate(BaseModel):
    slug: str
    title: str | None = None
    depends_on: list[str] = []
    priority: str = "P1"


def make_roadmap_router() -> APIRouter:
    router = APIRouter(tags=["roadmap"])

    @router.get("/api/projects/{project}/roadmap")
    def roadmap(project: str, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            features = model.list_features(conn, project)
            for f in features:
                f["tasks"] = model.list_tasks(conn, f["id"])
            return {"project": project, "features": features}
        finally:
            conn.close()

    @router.post("/api/projects/{project}/features", status_code=201)
    def add_feature(project: str, body: FeatureCreate, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            return model.add_feature(conn, project_slug=project, slug=body.slug, title=body.title)
        finally:
            conn.close()

    @router.post("/api/features/{project}/{feature}/tasks", status_code=201)
    def add_task(project: str, feature: str, body: TaskCreate, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            return model.add_task(conn, feature_ref=f"{project}/{feature}", slug=body.slug,
                                  title=body.title, depends_on=body.depends_on, priority=body.priority)
        finally:
            conn.close()

    @router.get("/api/features/{project}/{feature}/next")
    def next_task(project: str, feature: str, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            index = resolver.index_for_feature(conn, f"{project}/{feature}")
            nxt = resolver.resolve_next(index) if index else None
            return {"next": nxt, "n_tasks": len(index)}
        finally:
            conn.close()

    return router
