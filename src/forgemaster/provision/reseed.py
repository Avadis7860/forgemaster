"""reseed — re-matérialise les fichiers SCAFFOLD-OWNED d'un projet existant dans son SoT, en préservant le
travail worker. Première brique concrète du canal d'update préservant-le-travail (déclenchée-développeur,
manuelle, consentie — PAS d'auto-update).

Un projet est semé UNE fois à sa création (`registry.create_project` → `load_bundle(type)` → `init_sot`).
Quand le scaffold d'un type est corrigé APRÈS coup (ex. un Dockerfile cassé), le fix ne profite qu'aux projets
FUTURS — l'ancien porte la version cuite dans son SoT. `reseed` recompose `load_bundle(type)` et superpose sur
`dev` le sous-ensemble `reseed_owned` (le contrat de run possédé) via `git.overlay_commit` : seuls ces chemins
sont réécrits, tout le reste (`web/src`, `docs`, contenu, tasks) est **préservé par construction**. Idempotent
(aucun diff → aucun commit). `dev` seul avance ; `main` est intouché — une feature en vol hérite le fix à son
prochain rebase/redrain sur `dev`, OU via `--feature <slug>` (`overlay_commit` est worktree-safe : il resync
les chemins overlayés du worktree vivant, donc un preview-deploy de la feature sert bien le contrat corrigé).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.git.backend import GitBackend
from forgemaster.git.internal import InternalGit
from forgemaster.projects.registry import get_project
from forgemaster.provision import load_bundle, read_reseed_owned

# identité injectée, comme le seed initial (_seed_base)
_RESEED_IDENTITY = ("forgemaster", "forgemaster@localhost")


def reseed_project(conn: sqlite3.Connection, settings: Settings, *, project: str, branch: str = "dev",
                   git: GitBackend | None = None) -> dict:
    """Re-matérialise les fichiers `reseed_owned` du type de `project` sur `branch` de son SoT (défaut `dev`),
    préservant tout chemin worker. **Idempotent** : rien à mettre à jour → aucun commit (`updated=False`).
    Seule `branch` avance ; les autres branches (dont `main`) intouchées. `branch != dev` cible une **feature
    en vol** (`feature/<slug>`) pour lui livrer le contrat de run corrigé **sans redrain destructif** — le
    worker ne touche jamais les fichiers owned, donc l'overlay les remplace sans conflit et préserve son
    travail. L'overlay étant worktree-safe, le worktree vivant de la feature est resynchronisé sur les chemins
    owned → la feature est **immédiatement re-déployable/re-vérifiable** (pas d'attente de rebase).
    `KeyError` si le projet est absent (→ 404). `ValueError` si le type ne déclare aucun
    `reseed_owned` (rien de re-semable) ou si un fichier owned manque au bundle. Retour = compte-rendu."""
    git = git or InternalGit()
    proj = get_project(conn, project)                          # KeyError si absent → 404
    project_type = proj.get("project_type") or "generic"
    owned = read_reseed_owned(project_type)
    if not owned:
        raise ValueError(
            f"type {project_type!r} : aucun `reseed_owned` déclaré — rien à re-semer pour {project!r}")
    bundle = load_bundle(project_type)
    missing = [p for p in owned if p not in bundle]
    if missing:
        raise ValueError(f"bundle {project_type!r} : fichiers reseed_owned absents du bundle {missing}")
    payload = {path: bundle[path] for path in owned}
    sot = Path(proj["sot_path"])
    message = f"chore(scaffold): reseed contrat de run ({project_type}) — {len(payload)} fichier(s)"
    sha = git.overlay_commit(sot, branch=branch, files=payload, message=message, identity=_RESEED_IDENTITY)
    if sha is None:
        note = "scaffold déjà à jour — aucun changement"
    elif branch == "dev":
        note = (f"contrat de run re-semé sur `dev` ({len(payload)} fichier(s)) — les features en vol "
                f"héritent le fix à leur prochain redrain/rebase")
    else:
        note = (f"contrat de run re-semé sur `{branch}` ({len(payload)} fichier(s)) — feature corrigée "
                f"sans redrain, travail worker préservé")
    return {
        "project": project, "project_type": project_type, "branch": branch,
        "updated": sha is not None, "commit": sha, "files": sorted(payload), "note": note,
    }


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """`forgemaster scaffold reseed <project> [--feature <slug>]` — re-sème le contrat de run possédé dans le
    SoT.
    Défaut : branche `dev`. `--feature <slug>` cible `feature/<slug>` (feature en vol, sans redrain)."""
    feature = getattr(args, "feature", None)
    branch = f"feature/{feature}" if feature else "dev"
    conn = store.open_db(settings)
    try:
        try:
            report = reseed_project(conn, settings, project=args.project, branch=branch)
        except KeyError:
            print(f"erreur : projet {args.project!r} introuvable")
            return 1
        except ValueError as exc:
            print(f"erreur : {exc}")
            return 1
    finally:
        conn.close()
    mark = "✓" if report["updated"] else "•"
    print(f"{mark} scaffold {report['project']} ({report['project_type']}) — {report['note']}")
    if report["updated"]:
        print(f"  fichiers : {', '.join(report['files'])}  "
              f"(commit {report['commit'][:12]} sur {report['branch']})")
    return 0
