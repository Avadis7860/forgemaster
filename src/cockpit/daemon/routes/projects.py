"""routes/projects — router de domaine « registre des projets » (create/list/get). Router **fin** : il
délègue à `projects.registry` (couche portée), lit `Deps` par injection explicite, ne connaît aucun global.
Éclatement du monolithe orchestrator (#3) ; plus de `import server` (#1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cockpit.daemon.deps import Deps, get_deps
from cockpit.daemon.routes.templates import known_template_slugs, template_source_dir
from cockpit.design.apply import apply_template
from cockpit.dispatch import cost
from cockpit.projects import registry
from cockpit.secrets import cred_resolver


class ProjectCreate(BaseModel):
    slug: str
    name: str | None = None
    mirror_remote: str | None = None
    kind: str = "project"            # 'project' | 'tool' (validé par registry.create_project → 400 si autre)
    project_type: str = "generic"    # bundle semé (v6) : type registre-driven, validé → 400
    source_url: str | None = None    # adopter un repo existant (clone). Via l'API = repos PUBLICS ;
    #                                  l'adoption privée (avec credential) passe par `cockpit bootstrap` (P2).


class ProjectPatch(BaseModel):
    # PATCH partiel : seul le miroir GitHub est éditable pour l'instant (rendre un projet GitHub-backed).
    # `None` explicite = retirer le miroir ; champ absent = ne pas toucher.
    mirror_remote: str | None = None


class InspireRequest(BaseModel):
    template: str                    # slug d'un template de référence servi (validé contre la vitrine)


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
                                           mirror_remote=body.mirror_remote, kind=body.kind,
                                           source_url=body.source_url, project_type=body.project_type,
                                           cred_resolver=cred_resolver(deps.settings))
        finally:
            conn.close()

    @router.get("/{slug}")
    def get_project(slug: str, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            return registry.get_project(conn, slug)     # KeyError → 404 (handler global)
        finally:
            conn.close()

    @router.get("/{slug}/cost")
    def project_cost(slug: str, deps: Deps = Depends(get_deps)) -> dict:
        """Coût token agrégé du projet (total + par feature/step + overhead review/outillage). Le $ est celui
        de Claude (`total_cost_usd`), pas un recalcul. Projet absent → 404 (handler global)."""
        conn = deps.open_db()
        try:
            return cost.project_cost(conn, slug)        # KeyError → 404 (handler global)
        finally:
            conn.close()

    @router.post("/{slug}/inspire", status_code=201)
    def inspire_project(slug: str, body: InspireRequest, deps: Deps = Depends(get_deps)) -> dict:
        """Applique un template UI de référence de la vitrine à ce projet (« inspire mon projet de ce
        template »). Forme A : crée la feature+task de customisation `design-<template>` et sème la graine
        `docs/design/<template>/` (brief + tokens + preview) committée sur sa branche ; l'opérateur dispatch
        ensuite un worker qui customise le vrai `web/` (merge = revue UI + GO humain). **Pas de gate d'auth**
        (ne spawn pas `claude`). Template inconnu → 404 ; projet absent → 404 (KeyError, handler global)."""
        if body.template not in known_template_slugs():
            raise HTTPException(status_code=404, detail=f"template inconnu : {body.template!r}")
        conn = deps.open_db()
        try:
            return apply_template(conn, deps.settings, project=slug, template_slug=body.template,
                                  source_dir=template_source_dir(body.template))
        finally:
            conn.close()

    @router.patch("/{slug}")
    def patch_project(slug: str, body: ProjectPatch, deps: Deps = Depends(get_deps)) -> dict:
        """Édite un projet (partiel). Aujourd'hui : le **miroir GitHub** (rendre GitHub-backed) — `null`/vide
        le retire. Retourne le projet relu. Projet absent → 404 (handler global)."""
        conn = deps.open_db()
        try:
            return registry.set_mirror_remote(conn, slug, body.mirror_remote)
        finally:
            conn.close()

    return router
