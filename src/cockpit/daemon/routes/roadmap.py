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
    facet: str | None = None         # facette de dispatch — validée contre le bundle du projet → 400


class TaskCreate(BaseModel):
    slug: str
    title: str | None = None
    depends_on: list[str] = []
    priority: str = "P1"
    acceptance: str | None = None    # critères de DoD (v6) injectés au prompt worker


def make_roadmap_router() -> APIRouter:
    router = APIRouter(tags=["roadmap"])

    @router.get("/api/projects/{project}/roadmap")
    def roadmap(project: str, deps: Deps = Depends(get_deps)) -> dict:
        # Roadmap CLASSÉE : chaque task porte son état DAG (resolver.classify) et chaque feature son
        # NEXT dispatchable. Le graphe reste l'unique autorité de séquencement — le front n'infère
        # jamais l'état lui-même (pas de 2ᵉ résolveur en TS qui dériverait). Un seul GET = tout le board.
        conn = deps.open_db()
        try:
            features = model.list_features(conn, project)
            for f in features:
                tasks = model.list_tasks(conn, f["id"])
                index = {t["slug"]: t for t in tasks}
                classified = resolver.classify(index)
                f["tasks"] = [classified[t["slug"]] for t in tasks]   # ordre list_tasks préservé
                nxt = resolver.resolve_next(index) if index else None
                f["next"] = nxt["slug"] if nxt else None
            return {"project": project, "features": features}
        finally:
            conn.close()

    @router.post("/api/projects/{project}/features", status_code=201)
    def add_feature(project: str, body: FeatureCreate, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            return model.add_feature(conn, project_slug=project, slug=body.slug, title=body.title,
                                     facet=body.facet)
        finally:
            conn.close()

    @router.post("/api/features/{project}/{feature}/tasks", status_code=201)
    def add_task(project: str, feature: str, body: TaskCreate, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            return model.add_task(conn, feature_ref=f"{project}/{feature}", slug=body.slug,
                                  title=body.title, depends_on=body.depends_on, priority=body.priority,
                                  acceptance=body.acceptance)
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
