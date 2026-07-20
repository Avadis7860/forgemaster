"""routes/bundles — router de domaine « intérieur des bundles » : parcourir en LECTURE le contenu vendoré
d'un type de projet (l'arbre de fichiers ET le corps de chaque fichier), au-delà des seules métadonnées que
sert `routes/types` (`GET /api/types`). Gradue la « future surface de gestion des bundles (P5) » annoncée là.

Read-only, idempotent : le corps servi EST `provision.load_bundle(type)` — le bundle composé `base ⊕
overlay(type)` déjà matérialisé en mapping `chemin-relatif-POSIX → contenu` texte, caches/binaires exclus
(`_SKIP_DIRS`). Deux conséquences : (1) aucune lecture FS ad-hoc, aucun path-traversal possible (on ne fait
qu'un lookup de clé dans un dict en mémoire) ; (2) la vérité servie = **ce qu'un projet reçoit au seed**, ni
plus ni moins → introspection honnête « qu'embarque le cockpit » avant distribution. Goto-safe : le runner
goto-only de la boucle visuelle atteint ces GET sans effet.

Fail-closed : on ne parcourt QUE les types **offerts** (`list_valid_types`, même source que le dropdown de
création) — un bundle cassé n'apparaît pas ici non plus (404), on n'expose pas ce qu'on ne saurait pas semer.

La **curation** (regrouper l'arbre par ce qui porte la qualité vs la plomberie) est une classification
déterministe par chemin (`_classify`) : le serveur pose le `group` de chaque fichier ; le front rend les
groupes (vue curée) ou aplatit vers l'arbre complet (« tout voir »). Le libellé/ordre des groupes est une
affaire de présentation (front) ; le serveur ne possède que la taxonomie (les clés de groupe).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cockpit.provision import list_valid_types, load_bundle

# Groupes de curation (clés stables ; libellé + ordre côté front). Un fichier tombe dans le PREMIER match.
GROUP_METHOD = "method"      # facettes METHOD/PERSONA — le « comment » (ce qui porte la qualité)
GROUP_DEPLOY = "deploy"      # contrat de déploiement — manifeste, roadmap de lancement, compose/Docker
GROUP_SEED = "seed"          # source runnable semée — src/, web/, server/
GROUP_DOCS = "docs"          # prose de haut niveau — docs/, README/CLAUDE racine, README niché hors source
GROUP_PLUMBING = "plumbing"  # config d'outillage (tsconfig, eslint, lockfiles…) — repliée par défaut

_DEPLOY_FILES = frozenset({"compose.yaml", "Dockerfile"})


def _classify(path: str) -> str:
    """Le groupe de curation d'un chemin (POSIX, relatif au bundle). Déterministe, premier match. La taxonomie
    met en avant ce qui porte la qualité d'un bundle (méthode des facettes, contrat de deploy, seed runnable,
    docs) et relègue la plomberie d'outillage. Défaut : `plumbing`."""
    if path.startswith(".claude/facets/"):
        return GROUP_METHOD
    if path.startswith(".cockpit/") or path in _DEPLOY_FILES:
        return GROUP_DEPLOY
    if path.startswith(("src/", "web/", "server/")):
        return GROUP_SEED
    if path.startswith("docs/") or path == "CLAUDE.md" or path == "README.md" or path.endswith("/README.md"):
        return GROUP_DOCS
    return GROUP_PLUMBING


def _valid_types() -> set[str]:
    """Les types **offerts** (fail-closed, même source que le dropdown de création). Parcourir un type hors de
    cet ensemble = 404 : on n'introspecte que ce qu'on saurait semer."""
    return {t["type"] for t in list_valid_types()}


def make_bundles_router() -> APIRouter:
    router = APIRouter(prefix="/api/bundles", tags=["bundles"])

    @router.get("/{project_type}/tree")
    def bundle_tree(project_type: str) -> dict:
        """L'**arbre** d'un bundle : la liste triée de ses fichiers (chemins POSIX) avec leur `group` de
        curation. Source = `load_bundle` (bundle composé `base ⊕ overlay`, caches/binaires déjà exclus). Type
        inconnu ou cassé → 404 (fail-closed). GET idempotent (lecture de fichiers vendorés — goto-safe)."""
        if project_type not in _valid_types():
            raise HTTPException(
                status_code=404, detail=f"type de bundle inconnu ou invalide : {project_type!r}")
        files = load_bundle(project_type)
        return {
            "type": project_type,
            "files": [{"path": p, "group": _classify(p)} for p in sorted(files)],
        }

    @router.get("/{project_type}/file")
    def bundle_file(project_type: str, path: str) -> dict:
        """Le **corps** d'UN fichier du bundle (`path` = chemin POSIX exact rendu par `/tree`). Lookup de clé
        dans `load_bundle` → aucun path-traversal possible (on ne touche pas le FS). Type inconnu/cassé ou
        fichier absent → 404. GET idempotent (goto-safe)."""
        if project_type not in _valid_types():
            raise HTTPException(
                status_code=404, detail=f"type de bundle inconnu ou invalide : {project_type!r}")
        files = load_bundle(project_type)
        if path not in files:
            raise HTTPException(
                status_code=404, detail=f"fichier absent du bundle {project_type!r} : {path!r}")
        return {"type": project_type, "path": path, "group": _classify(path), "content": files[path]}

    return router
