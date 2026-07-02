"""worktree — cycle de vie d'un worktree de feature : réservation (worktree + port couplé), teardown,
audit. Un worktree = **le mutex** d'une feature (1 worker à la fois) ; N features ⇒ N worktrees parallèles.

Specs : `worktree-cleanup-at-merge` (cleanup worktree **AVANT** `git branch -D` ; port↔worktree couplé,
relâché au merge ET au reset) + `sot-local-worker-vs-clone-split` (worktree **attaché au SoT partagé** ;
concurrence sérialisée par **flock** sur le `.git` du bare, dans `git/internal`, refactor **#12**).

Port : concept du broker de ports `services/aggregator/routers/devserver.py` + `worktree_dispatch.py`, mais
la mécanique git (add/remove sous flock) vit dans `git/internal` (déjà porté) et le port dans `dispatch/
ports` : ici on ne fait que **composer** les deux et coupler leur cycle de vie.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cockpit.config import Settings
from cockpit.dispatch import ports
from cockpit.git.backend import GitBackend
from cockpit.projects.registry import sot_path_for
from cockpit.roadmap import model

WORKTREE_BASE = "dev"   # les features partent de dev (main suit dev) — cf. spec forge-sot-local


def worktree_path_for(settings: Settings, project: str, feature: str) -> Path:
    """Chemin du worktree d'une feature : `<projects_root>/<project>/worktrees/<feature>`. Déterministe."""
    return settings.projects_root / project / "worktrees" / feature


def _purpose(feature: str) -> str:
    return f"worktree:{feature}"


def reserve(conn: sqlite3.Connection, settings: Settings, git: GitBackend, *,
            project: str, feature: str, probe: ports.Probe | None = ports.local_probe) -> dict:
    """Réserve (idempotent) un worktree **attaché au SoT** + un port pour `feature`. Retourne
    `{path, port, branch}`. Le worktree est créé sur `feature/<slug>` ancré sur `dev` (flock dans
    `git/internal`) ; le port est stable au re-provision (`ports.reserve`)."""
    feat = model.resolve_feature(conn, f"{project}/{feature}")   # KeyError si feature absente
    sot = sot_path_for(settings, project)
    wt = worktree_path_for(settings, project, feature)
    if not wt.exists():
        git.add_worktree(sot, wt, branch=feat["branch"], base=WORKTREE_BASE)
    res = ports.reserve(conn, project=project, purpose=_purpose(feature), probe=probe)
    conn.execute("UPDATE features SET worktree_path = ?, status = 'active' WHERE id = ?",
                 (str(wt), feat["id"]))
    conn.commit()
    return {"path": wt, "port": res["port"], "branch": feat["branch"]}


def release(conn: sqlite3.Connection, settings: Settings, git: GitBackend, *,
            project: str, feature: str) -> None:
    """Teardown : `remove_worktree` **PUIS** relâche le port (spec worktree-cleanup). Appelé au merge ET au
    reset. Idempotent (worktree déjà retiré / port déjà relâché ne lèvent pas). Ne supprime PAS la branche —
    c'est à l'appelant (`gate.merge`) de faire `delete_branch` **après** ce release (ordre spec)."""
    sot = sot_path_for(settings, project)
    wt = worktree_path_for(settings, project, feature)
    if wt.exists():
        git.remove_worktree(sot, wt)
    ports.release(conn, project=project, purpose=_purpose(feature))
    feat = conn.execute("SELECT id FROM features WHERE project_id = "
                        "(SELECT id FROM projects WHERE slug = ?) AND slug = ?",
                        (project, feature)).fetchone()
    if feat is not None:
        conn.execute("UPDATE features SET worktree_path = NULL WHERE id = ?", (feat["id"],))
        conn.commit()


def audit(conn: sqlite3.Connection, settings: Settings) -> list[dict]:
    """Audit d'orphelins (doit rester à 0 après merge/reset) : port réservé sans worktree sur disque, ou
    worktree sur disque sans réservation de port. Retourne la liste des anomalies `{kind, project, …}`."""
    orphans: list[dict] = []
    reserved = ports.list_reservations(conn)
    reserved_keys = {(r["project"], r["purpose"]) for r in reserved}
    for r in reserved:
        feature = r["purpose"].removeprefix("worktree:")
        wt = worktree_path_for(settings, r["project"], feature)
        if not wt.exists():
            orphans.append({"kind": "port-sans-worktree", "project": r["project"],
                            "feature": feature, "port": r["port"]})
    # worktrees sur disque sans réservation de port
    for proj_dir in _iter_worktree_dirs(settings):
        project, feature = proj_dir
        if (project, _purpose(feature)) not in reserved_keys:
            orphans.append({"kind": "worktree-sans-port", "project": project, "feature": feature})
    return orphans


def _iter_worktree_dirs(settings: Settings) -> list[tuple[str, str]]:
    """Liste `(project, feature)` des worktrees présents sous `<projects_root>/*/worktrees/*`."""
    out: list[tuple[str, str]] = []
    root = settings.projects_root
    if not root.exists():
        return out
    for project_dir in root.iterdir():
        wt_root = project_dir / "worktrees"
        if wt_root.is_dir():
            out.extend((project_dir.name, wt.name) for wt in wt_root.iterdir() if wt.is_dir())
    return out
