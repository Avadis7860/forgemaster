"""content.ingest — livraison **worktree-aware** d'un asset uploadé (règle #3 de la spec). Compose la
résolution du worktree + l'écriture (`content.upload.write_project_upload`) + le commit forge, à l'image de
`design.apply.apply_template`. `GitBackend` **injecté** ; **transport local** (`core.run`, zéro
ssh/proxmox/CT). Ce module ne touche par ailleurs aucun réseau — c'est un fait sur lui, pas le contenu de
l'invariant, qui interdit d'orchestrer des hôtes distants et non de parler au réseau.

Deux cas, une seule discipline forge (jamais d'acte outward autonome) :

- **Worktree actif** (feature `status='active'` du projet, avec un worktree sur disque — ex. l'interview
  `socle`) → écrit **dans ce worktree** (Read **live** par la session en cours) + `commit_worktree` sur **sa**
  branche. L'asset devient project-wide au merge human-GO **standard** de cette feature.
- **Aucun worktree actif** (moment B, projet en cours) → réserve une feature éphémère `content-<x>` sur `dev`
  (`worktree.reserve`, comme `apply_template`) + `commit_worktree`. Le merge reste **human-GO, fail-closed**
  (jamais mergé ici). **Jamais** de commit direct sur `dev`.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.content.upload import write_project_upload
from forgemaster.dispatch import worktree
from forgemaster.git.backend import GitBackend
from forgemaster.git.identity import resolve_identity
from forgemaster.git.internal import InternalGit
from forgemaster.projects.registry import get_project
from forgemaster.roadmap import model

CONTENT_FEATURE_PREFIX = "content-"


def _content_feature_slug(filename: str) -> str:
    """Slug **déterministe** de la feature éphémère d'un asset : `content-<kebab(stem)>` (moment B). Kebab
    strict (validé ensuite par `ids.ensure_slug` via `add_feature`) ; stem vide/non-alphanumérique → `asset`.
    Deux uploads du même nom réutilisent la même feature (idempotent : commit no-op si contenu identique)."""
    stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.lower()).strip("-")
    return f"{CONTENT_FEATURE_PREFIX}{stem or 'asset'}"


def _resolve_active_worktree(conn: sqlite3.Connection, project: str,
                             feature: str | None) -> tuple[str, Path] | None:
    """Résout le worktree **actif** dans lequel écrire en live (règle #3, 1er cas). `feature` explicite →
    cette feature, si elle est `active` avec un worktree sur disque. Sinon auto : la 1ère feature `active`
    (ordre déterministe par slug, `list_features`) dont le `worktree_path` existe — `None` sinon (→ forge).
    Le câblage UI (onboarding) passera la feature explicitement ; l'auto sert la résolution à 1 actif."""
    feats = model.list_features(conn, project)
    if feature is not None:
        match = [f for f in feats if f["slug"] == feature]
        if not match:
            raise KeyError(f"{project}/{feature}")
        candidates = [f for f in match if f["status"] == "active" and f["worktree_path"]]
    else:
        candidates = [f for f in feats if f["status"] == "active" and f["worktree_path"]]
    for f in candidates:
        wt = Path(f["worktree_path"])
        if wt.exists():
            return f["slug"], wt
    return None


def ingest_upload(conn: sqlite3.Connection, settings: Settings, git: GitBackend | None = None, *,
                  project: str, filename: str, data: bytes, dest_slug: str = "brand",
                  feature: str | None = None) -> dict:
    """Livre l'asset `filename` (`data`) au projet `project` sous `docs/design/<dest_slug>/`. Retourne
    `{project, mode, feature, branch, path, file, commit, merged}` où `mode` ∈ `noop|live|forge` :

    - **noop** — `data` vide → rien écrit, aucune feature créée (symétrie `write_design_seed`) ;
    - **live** — worktree actif présent → écrit dedans (Read live) + `commit_worktree` sur sa branche,
      `merged=False` (project-wide au merge GO standard de cette feature) ;
    - **forge** — aucun worktree actif → feature éphémère `content-<x>` réservée + `commit_worktree`,
      `merged=False` (merge **human-GO, fail-closed** — jamais ici).

    `git` **injecté** (défaut `InternalGit`). Lève `KeyError` si le projet (ou une `feature` explicite
    inconnue) est absent ; les bornes de `write_project_upload` remontent telles quelles (413/415/400)."""
    git = git or InternalGit()
    get_project(conn, project)                       # KeyError si projet absent (→ 404 handler global)
    if not data:
        return {"project": project, "mode": "noop", "feature": None, "branch": None,
                "path": None, "file": None, "commit": None, "merged": False}

    identity = resolve_identity(project, worktree.WORKTREE_BASE, role="worker")
    message = f"content({dest_slug}): dépose l'asset {Path(filename).name}"

    active = _resolve_active_worktree(conn, project, feature)
    if active is not None:
        feat_slug, wt = active
        target = write_project_upload(wt, filename=filename, data=data, dest_slug=dest_slug)
        commit = git.commit_worktree(wt, message=message, identity=identity)
        branch = model.resolve_feature(conn, f"{project}/{feat_slug}")["branch"]
        return {"project": project, "mode": "live", "feature": feat_slug, "branch": branch,
                "path": str(wt), "file": str(target) if target else None,
                "commit": commit, "merged": False}

    # Aucun worktree actif → voie forge : feature éphémère content-<x> (jamais de commit direct sur dev).
    content_slug = _content_feature_slug(filename)
    if content_slug not in {f["slug"] for f in model.list_features(conn, project)}:
        model.add_feature(conn, project_slug=project, slug=content_slug,
                          title=f"Dépôt d'asset : {Path(filename).name}")
    res = worktree.reserve(conn, settings, git, project=project, feature=content_slug)
    target = write_project_upload(res["path"], filename=filename, data=data, dest_slug=dest_slug)
    commit = git.commit_worktree(res["path"], message=message, identity=identity)
    return {"project": project, "mode": "forge", "feature": content_slug, "branch": res["branch"],
            "path": str(res["path"]), "file": str(target) if target else None,
            "commit": commit, "merged": False}
