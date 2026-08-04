"""routes/docs — router de domaine « docs » : la **carte** d'un projet/outil, lue depuis SON repo (SoT bare),
rendue par l'onglet Docs du forgemaster. La doc vit dans le repo (`docs/tool-card.md`), pas dans le
forgemaster
→ **SoT-and-derive** : mettre à jour la carte = éditer le repo, le forgemaster la relit.

Read-only, bare-safe (réutilise `InternalGit.read_blob`, aucun working-tree requis — les outils sont bare).
Fichier canonique `docs/tool-card.md` ; repli `README.md` ; sinon `found:false` (l'UI affiche un EmptyState).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from forgemaster.daemon.deps import Deps, get_deps
from forgemaster.git.internal import GitOpError, InternalGit
from forgemaster.projects.registry import get_project

CANONICAL_DOC = "docs/tool-card.md"   # la carte dédiée « ce que c'est · pourquoi avec Claude »
FALLBACK_DOC = "README.md"            # repli si pas de carte (orienté dev, mais mieux que rien)
_PREFERRED_REFS = ("main", "dev")     # main suit dev ; on lit la branche publiée par défaut


def make_docs_router() -> APIRouter:
    router = APIRouter(tags=["docs"])

    def _sot(deps: Deps, project: str) -> Path:
        conn = deps.open_db()
        try:
            return Path(get_project(conn, project)["sot_path"])
        finally:
            conn.close()

    @router.get("/api/projects/{project}/docs")
    def project_docs(project: str, ref: str | None = None, deps: Deps = Depends(get_deps)) -> dict:
        """Carte docs d'un projet lue depuis son SoT. `ref` optionnel (défaut : `main`→`dev`→1ʳᵉ branche).
        Renvoie `{project, found, ref, path, content, truncated}`. `found:false` (aucune carte/README) est
        rendu tel quel — l'UI affiche « pas encore de page docs », ce n'est pas une erreur. Projet absent
        → 404 ; SoT illisible → 422. Idempotent (goto-safe pour la boucle visuelle)."""
        sot = _sot(deps, project)
        git = InternalGit()
        try:
            names = {b["name"] for b in git.branches(sot)}
        except GitOpError as exc:
            raise HTTPException(status_code=422, detail=f"lecture git impossible : {exc}") from exc
        if not names:
            return {"project": project, "found": False, "ref": None, "path": None,
                    "content": "", "truncated": False}
        chosen = ref or next((r for r in _PREFERRED_REFS if r in names), None) or sorted(names)[0]
        for candidate in (CANONICAL_DOC, FALLBACK_DOC):
            try:
                blob = git.read_blob(sot, chosen, candidate)
            except GitOpError:
                continue                                  # fichier absent à cette réf → candidat suivant
            if blob.get("binary") or blob.get("too_large"):
                continue                                  # une doc Markdown n'est ni binaire ni géante
            return {"project": project, "found": True, "ref": chosen, "path": candidate,
                    "content": blob["content"], "truncated": bool(blob.get("truncated"))}
        return {"project": project, "found": False, "ref": chosen, "path": None,
                "content": "", "truncated": False}

    return router
