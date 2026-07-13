"""routes/deployments — router de domaine « deployments » : visibilité read-only des **2 déploiements**
(`main`/`dev`) d'un projet **+** le **lifecycle mutant** (deploy/stop/restart), branché sur `runtime/` (P2).
Le GET reste **idempotent/pur-DB** (deep-link goto-only sûr) ; up/down/restart sont des **POST** (mutations,
jamais atteintes par un runner goto-only). Répond « où en sont mes déploiements ? » et « fais tourner »."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from cockpit.daemon.deps import Deps, get_deps
from cockpit.projects.deployments import list_deployments
from cockpit.projects.registry import get_project
from cockpit.runtime import engine

# surface publique exposée (champs internes gardés : id, project_id, compose_ref, created_at, updated_at)
_FIELDS = ("branch", "status", "port", "url", "last_deploy_sha")


def _public(d: dict) -> dict:
    return {k: d[k] for k in _FIELDS}


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
        return {"project": project, "deployments": [_public(d) for d in rows]}

    @router.get("/api/projects/{project}/deployments/{branch}/status")
    def deployment_status(project: str, branch: str, deps: Deps = Depends(get_deps)) -> dict:
        """État **live** d'un déploiement : réconcilie la DB avec `compose ps` (`running`/`stopped`, ou
        `unhealthy` si `ps` échoue ; `no_deploy` rendu tel quel — jamais un faux-vert). **Séparé** du GET
        liste pur (goto-safe) : ce GET fait un appel `compose` réel, l'UI le rattache au RefreshButton, jamais
        au polling d'un runner *goto-only* (calque `GET /git/sync` vs `GET /git`). Projet absent → 404,
        branche invalide → 400."""
        conn = deps.open_db()
        try:
            dep = engine.status(conn, deps.settings, slug=project, branch=branch)
        finally:
            conn.close()
        return {"project": project, "deployment": _public(dep)}

    @router.get("/api/projects/{project}/deployments/{branch}/logs")
    def deployment_logs(project: str, branch: str, tail: int = Query(200, ge=1, le=1000),
                        deps: Deps = Depends(get_deps)) -> dict:
        """Les `tail` dernières lignes de logs d'un déploiement (`compose logs --tail`, borné) — read-only.
        **Vide honnête** : un déploiement jamais monté rend `lines: []` (jamais une erreur ni un faux-vert).
        Idempotent au sens observation (ne mute pas l'état) → deep-link *goto-only* sûr. Projet absent → 404,
        branche invalide → 400."""
        conn = deps.open_db()
        try:
            out = engine.logs(conn, deps.settings, slug=project, branch=branch, tail=tail)
        finally:
            conn.close()
        return {"project": project, "branch": branch, "lines": out["lines"]}

    @router.post("/api/projects/{project}/deployments/{branch}/up")
    def deployment_up(project: str, branch: str, deps: Deps = Depends(get_deps)) -> dict:
        """Déploie (ou re-déploie) `(project, branch)` : build + `compose up -d` sur le compose-project
        isolé → `running` (+ port, url, sha). **POST**. Projet absent → 404 ; branche/échec build → 400."""
        return _mutate(deps, engine.deploy, project, branch)

    @router.post("/api/projects/{project}/deployments/{branch}/down")
    def deployment_down(project: str, branch: str, deps: Deps = Depends(get_deps)) -> dict:
        """Arrête `(project, branch)` (`compose down`) → `stopped` (port gardé, URL stable). **POST**."""
        return _mutate(deps, engine.stop, project, branch)

    @router.post("/api/projects/{project}/deployments/{branch}/restart")
    def deployment_restart(project: str, branch: str, deps: Deps = Depends(get_deps)) -> dict:
        """Redémarre les conteneurs de `(project, branch)` (`compose restart`) → `running`. **POST**."""
        return _mutate(deps, engine.restart, project, branch)

    return router


def _mutate(deps: Deps, op, project: str, branch: str) -> dict:
    """Exécute une op de lifecycle (`engine.deploy|stop|restart`) et rend le déploiement projeté à la
    surface publique. `KeyError` (projet absent) → 404, `ValueError` (branche invalide / échec compose) → 400
    (handlers globaux). La connexion est refermée dans tous les cas."""
    conn = deps.open_db()
    try:
        dep = op(conn, deps.settings, slug=project, branch=branch)
    finally:
        conn.close()
    return {"project": project, "deployment": _public(dep)}
