"""routes/projects — router de domaine « registre des projets » (create/list/get). Router **fin** : il
délègue à `projects.registry` (couche portée), lit `Deps` par injection explicite, ne connaît aucun global.
Éclatement du monolithe orchestrator (#3) ; plus de `import server` (#1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from cockpit.content.ingest import ingest_upload
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

    @router.post("/{slug}/upload", status_code=201)
    async def upload_to_project(slug: str, file: UploadFile = File(...), dest: str = Form("brand"),
                               feature: str | None = Form(None),
                               deps: Deps = Depends(get_deps)) -> dict:
        """Dépose un asset (`file`) dans le projet sous `docs/design/<dest>/`, lisible par le worker/l'IA
        d'interview. Multipart : `file` (binaire), `dest` (sous-dossier, défaut `brand`), `feature` (optionnel
        — cible un worktree actif nommé). Livraison **worktree-aware** (même cœur que la CLI) : worktree actif
        → écrit dedans + commit sur sa branche (Read live) ; sinon voie forge (feature éphémère `content-<x>`,
        merge **GO humain**). **Aucun secret** par ce canal (rejeté → 400). Projet/feature absents → 404 ;
        secret/traversal/nom → 400 ; taille > borne → **413** ; type hors allow-list → **415** (handlers
        globaux). Ne spawn pas `claude`."""
        data = await file.read()
        conn = deps.open_db()
        try:
            return ingest_upload(conn, deps.settings, project=slug, filename=file.filename or "",
                                 data=data, dest_slug=dest, feature=feature)
        finally:
            conn.close()

    @router.post("/{slug}/scaffold/reseed")
    def reseed_scaffold(slug: str, deps: Deps = Depends(get_deps)) -> dict:
        """Re-matérialise les fichiers **scaffold-owned** (contrat de run) du type du projet dans son SoT
        (`dev`), en préservant le travail worker (`web/`, contenu, docs). **Idempotent** (rien à jour → aucun
        commit). `main`/worktrees en vol intouchés (une feature hérite le fix à son prochain redrain). **Pas
        de gate d'auth** (aucun spawn `claude`, mutation git locale — comme redrain). Projet absent → 404 ;
        type sans `reseed_owned` → 400 (ValueError, handler global)."""
        from cockpit.provision import reseed
        conn = deps.open_db()
        try:
            return reseed.reseed_project(conn, deps.settings, project=slug)
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
