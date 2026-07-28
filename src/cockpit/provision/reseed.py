"""reseed — re-matérialise les fichiers SCAFFOLD-OWNED d'un projet existant dans son SoT, en préservant le
travail worker. Première brique concrète du canal d'update préservant-le-travail (déclenchée-développeur,
manuelle, consentie — PAS d'auto-update).

Un projet est semé UNE fois à sa création (`registry.create_project` → `load_bundle(type)` → `init_sot`).
Quand le scaffold d'un type est corrigé APRÈS coup (ex. un Dockerfile cassé), le fix ne profite qu'aux projets
FUTURS — l'ancien porte la version cuite dans son SoT. `reseed` recompose `load_bundle(type)` et superpose sur
`dev` le sous-ensemble `reseed_owned` (le contrat de run possédé) via `git.overlay_commit` : seuls ces chemins
sont réécrits, tout le reste (`web/src`, `docs`, contenu, tasks) est **préservé par construction**. Idempotent
(aucun diff → aucun commit). `dev` seul avance ; `main` et les worktrees de features en vol sont intouchés —
une feature hérite le fix à son prochain rebase/redrain sur `dev`.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from cockpit.config import Settings
from cockpit.db import store
from cockpit.git.backend import GitBackend
from cockpit.git.internal import InternalGit
from cockpit.projects.registry import get_project
from cockpit.provision import load_bundle, read_reseed_owned

_RESEED_IDENTITY = ("cockpit", "cockpit@localhost")   # identité injectée, comme le seed initial (_seed_base)


def reseed_project(conn: sqlite3.Connection, settings: Settings, *, project: str,
                   git: GitBackend | None = None) -> dict:
    """Re-matérialise les fichiers `reseed_owned` du type de `project` dans son SoT (`dev`), préservant tout
    chemin worker. **Idempotent** : rien à mettre à jour → aucun commit (`updated=False`). `dev` seul avance ;
    `main`/worktrees en vol intouchés. `KeyError` si le projet est absent (→ 404). `ValueError` si le type ne
    déclare aucun `reseed_owned` (rien de re-semable) ou si un fichier owned manque au bundle. Retour =
    compte-rendu (contrat front)."""
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
    sha = git.overlay_commit(sot, branch="dev", files=payload, message=message, identity=_RESEED_IDENTITY)
    return {
        "project": project, "project_type": project_type, "branch": "dev",
        "updated": sha is not None, "commit": sha, "files": sorted(payload),
        "note": ("scaffold déjà à jour — aucun changement" if sha is None else
                 f"contrat de run re-semé sur `dev` ({len(payload)} fichier(s)) — les features en vol "
                 f"héritent le fix à leur prochain redrain/rebase"),
    }


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """`cockpit scaffold reseed <project>` — re-sème le contrat de run possédé dans le SoT (`dev`)."""
    conn = store.open_db(settings)
    try:
        try:
            report = reseed_project(conn, settings, project=args.project)
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
        print(f"  fichiers : {', '.join(report['files'])}  (commit {report['commit'][:12]} sur dev)")
    return 0
