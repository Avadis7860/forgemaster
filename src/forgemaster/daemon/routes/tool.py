"""routes/tool — router de domaine « outils adoptés » (`kind=tool`). Une seule op, **mutation gatée par
construction** : `POST .../tool/sync` re-synchronise un outil avec son amont GitHub **pull-only ff-only**
(jamais de push ni de merge non-ff — frontière read-only stricte, spec forge-sot-local). Délègue à
`forgemaster.toolsync` ; router fin.

**Fail-close symétrique** : la route ne cible QUE `kind=tool`. Un projet (`kind=project`, SoT autoritatif)
la **refuse** → 409 (`NotAToolError`) : sa voie de sync est la réconciliation gatée `reconcile` (P5). Le
handler `ValueError→400` global mapperait `NotAToolError` (sous-classe) en 400 : on l'**intercepte** donc ici
pour le vrai 409. Entité absente → 404 ; op git dure → 422."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from forgemaster.daemon.deps import Deps, get_deps
from forgemaster.git.internal import GitOpError
from forgemaster.toolsync import NotAToolError, sync_tool


def make_tool_router() -> APIRouter:
    router = APIRouter(tags=["tool"])

    @router.post("/api/projects/{project}/tool/sync")
    def tool_sync(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Re-synchronise l'outil `project` avec son amont (`origin`) **pull-only ff** : fetch + avance les
        refs suivies (`dev`, `main`) quand l'amont est en avance, **sans jamais pousser** ni merger non-ff.
        Auto-répare le refspec de fetch d'un clone bare (dégèle un outil adopté figé). Après un sync qui
        bouge, pré-chauffe best-effort l'index Flow. **Réseau, non-idempotent** (fetch) — hors GET no-poll.
        Renvoie `{project, slug, kind, remote, fetched, actions, changed, blocked, state, index_refreshed}`.
        **Fail-close** : entité qui n'est pas un outil → 409 (un projet passe par la réconciliation gatée) ;
        entité absente → 404 ; op git dure (ref corrompue…) → 422."""
        conn = deps.open_db()
        try:
            report = sync_tool(conn, deps.settings, slug=project)
        except NotAToolError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"outil introuvable : {project}") from exc
        except GitOpError as exc:
            raise HTTPException(status_code=422, detail=f"sync git impossible : {exc}") from exc
        finally:
            conn.close()
        return {"project": project, **report}

    return router
