"""routes/git — router de domaine « git » (visibilité read-only sur le SoT bare d'un projet : branches,
avance/retard `main` vs `dev`, log court par réf protégée). **AUCUNE mutation** : le SoT est un bare (pas de
working-tree) et le cycle git mutant vit dans `gate/merge` ; ce routeur ne fait que lire. Sert la question
« où en est main vs dev ? » posée par les instances de vue (main rattrape dev)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from cockpit.daemon.deps import Deps, get_deps
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects.registry import get_project

_LOG_N = 20  # profondeur du log par réf (récents d'abord)


def make_git_router() -> APIRouter:
    router = APIRouter(tags=["git"])

    @router.get("/api/projects/{project}/git")
    def git_view(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Vue git read-only du SoT bare : branches (nom·sha court·sujet), avance/retard `main` vs `dev`
        (le signal « main rattrape dev »), et log court par réf protégée présente. Idempotent (aucune
        mutation) — le runner de boucle visuelle *goto-only* l'atteint sans risque. Projet absent → 404 ;
        SoT illisible → 422 (jamais un demi-état inventé)."""
        conn = deps.open_db()
        try:
            sot = Path(get_project(conn, project)["sot_path"])   # KeyError → 404 (handler global)
        finally:
            conn.close()
        git = InternalGit()
        try:
            branches = git.branches(sot)
            names = {b["name"] for b in branches}
            logs = {ref: git.log(sot, ref, n=_LOG_N) for ref in ("dev", "main") if ref in names}
            # ahead/behind seulement si les deux réfs protégées existent (un SoT neuf les a toutes deux).
            ahead_behind = (
                git.ahead_behind(sot, base="main", head="dev") if {"dev", "main"} <= names else None
            )
        except GitOpError as exc:
            raise HTTPException(status_code=422, detail=f"lecture git impossible : {exc}") from exc
        return {"project": project, "branches": branches, "ahead_behind": ahead_behind, "logs": logs}

    return router
