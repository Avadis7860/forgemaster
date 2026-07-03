"""routes/git — router de domaine « git » (visibilité read-only sur le SoT bare d'un projet : branches,
avance/retard `main` vs `dev`, log court, **et exploration de l'arbre + contenu d'un fichier**). **AUCUNE
mutation** : le SoT est un bare (pas de working-tree) et le cycle git mutant vit dans `gate/merge` ; ce
routeur ne fait que lire. Sert « où en est main vs dev ? » ET « voyager dans les fichiers du dépôt »."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from cockpit.daemon.deps import Deps, get_deps
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects.registry import get_project

_LOG_N = 20  # profondeur du log par réf (récents d'abord)


def make_git_router() -> APIRouter:
    router = APIRouter(tags=["git"])

    def _sot(deps: Deps, project: str) -> Path:
        """Résout le chemin du SoT bare d'un projet (KeyError projet absent → 404 handler global)."""
        conn = deps.open_db()
        try:
            return Path(get_project(conn, project)["sot_path"])
        finally:
            conn.close()

    @router.get("/api/projects/{project}/git")
    def git_view(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Vue git read-only du SoT bare : branches (nom·sha court·sujet), avance/retard `main` vs `dev`
        (le signal « main rattrape dev »), et log court par réf protégée présente. Idempotent (aucune
        mutation) — le runner de boucle visuelle *goto-only* l'atteint sans risque. Projet absent → 404 ;
        SoT illisible → 422 (jamais un demi-état inventé)."""
        sot = _sot(deps, project)
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

    @router.get("/api/projects/{project}/git/tree")
    def git_tree(project: str, ref: str = "dev", path: str = "", deps: Deps = Depends(get_deps)) -> dict:
        """Entrées d'un dossier du dépôt à une réf (dossiers d'abord). `ref` défaut `dev`, `path` vide =
        racine. Read-only, idempotent (goto-only safe). Projet absent → 404 ; réf/chemin introuvable ou
        `path` non-dossier → 404 (la ressource demandée n'existe pas à cette réf)."""
        sot = _sot(deps, project)
        try:
            entries = InternalGit().ls_tree(sot, ref, path)
        except GitOpError as exc:
            raise HTTPException(status_code=404, detail=f"arbre introuvable ({ref}:{path}) : {exc}") from exc
        return {"project": project, "ref": ref, "path": path, "entries": entries}

    @router.get("/api/projects/{project}/git/blob")
    def git_blob(project: str, ref: str, path: str, deps: Deps = Depends(get_deps)) -> dict:
        """Contenu d'un fichier du dépôt à une réf. `ref` et `path` requis. Renvoie type/taille + contenu
        texte (ou drapeaux `binary`/`too_large`/`truncated` — jamais d'octets bruts, cf. gardes L4).
        Read-only, idempotent. Projet absent → 404 ; réf/chemin introuvable ou non-blob → 404."""
        sot = _sot(deps, project)
        try:
            blob = InternalGit().read_blob(sot, ref, path)
        except GitOpError as exc:
            raise HTTPException(
                status_code=404, detail=f"fichier introuvable ({ref}:{path}) : {exc}") from exc
        return {"project": project, **blob}

    @router.get("/api/projects/{project}/git/commit/{sha}")
    def git_commit(project: str, sha: str, deps: Deps = Depends(get_deps)) -> dict:
        """Détail d'un commit (métadonnées + fichiers touchés avec `+/-` par fichier). Read-only,
        idempotent (goto-only safe). Projet absent → 404 ; sha/réf introuvable → 404."""
        sot = _sot(deps, project)
        try:
            detail = InternalGit().commit_detail(sot, sha)
        except GitOpError as exc:
            raise HTTPException(status_code=404, detail=f"commit introuvable ({sha}) : {exc}") from exc
        return {"project": project, **detail}

    @router.get("/api/projects/{project}/git/diff")
    def git_diff(
        project: str, base: str, head: str, deps: Deps = Depends(get_deps),
    ) -> dict:
        """Diff unifié d'une feature (`base...head`, three-dot = depuis la merge-base). Read-only,
        idempotent. Projet absent → 404 ; une réf introuvable → 404."""
        sot = _sot(deps, project)
        git = InternalGit()
        try:
            text = git.diff_text(sot, base=base, head=head)
            names = git.diff_names(sot, base=base, head=head)
        except GitOpError as exc:
            raise HTTPException(
                status_code=404, detail=f"diff impossible ({base}...{head}) : {exc}") from exc
        return {"project": project, "base": base, "head": head, "files": names, "diff": text}

    @router.get("/api/projects/{project}/git/history")
    def git_history(
        project: str, ref: str, path: str, deps: Deps = Depends(get_deps),
    ) -> dict:
        """Historique des commits touchant un fichier à une réf (récents d'abord). Read-only, idempotent.
        Projet absent → 404 ; réf introuvable → 404 ; fichier sans historique → liste vide (200)."""
        sot = _sot(deps, project)
        try:
            commits = InternalGit().file_history(sot, ref, path)
        except GitOpError as exc:
            raise HTTPException(
                status_code=404, detail=f"historique introuvable ({ref}:{path}) : {exc}") from exc
        return {"project": project, "ref": ref, "path": path, "commits": commits}

    return router
