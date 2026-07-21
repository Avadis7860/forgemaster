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
from cockpit.git.identity import resolve_identity
from cockpit.projects.registry import sot_path_for
from cockpit.provision import facet as facet_mod
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
    `git/internal`) ; le port est stable au re-provision (`ports.reserve`).

    Réalignement anti-stale-base (symétrique de `run_merge`, spec sot-local) : un worktree **déjà sur
    disque** — fabriqué par un run antérieur **interrompu** avant que `dev` ne porte le socle/merge — reste
    ancré sur un `dev` périmé même après que `dev` a avancé, et le worker coderait contre l'ancienne base.
    Quand `dev` n'est plus ancêtre de `feature/<slug>`, on **rebase** la branche sur le `dev` à jour
    (linéaire, **préserve** les commits worker) ; conflit non trivial → `rebase_onto` fait `rebase --abort`
    puis lève `GitOpError` (jamais d'écrasement silencieux — la feature doit être re-drainée). Base==dev ⇒
    `is_ancestor` vrai ⇒ rebase sauté ⇒ réutilisation idempotente (non-régression)."""
    feat = model.resolve_feature(conn, f"{project}/{feature}")   # KeyError si feature absente
    sot = sot_path_for(settings, project)
    wt = worktree_path_for(settings, project, feature)
    if not wt.exists():
        git.add_worktree(sot, wt, branch=feat["branch"], base=WORKTREE_BASE)
    elif not git.is_ancestor(sot, WORKTREE_BASE, feat["branch"]):
        # worktree pré-existant sur base divergée (run interrompu avant l'avancée de dev) → réaligne
        git.rebase_onto(sot, wt, onto=WORKTREE_BASE,
                        identity=resolve_identity(project, WORKTREE_BASE, role="worker"))
    # Activer la facette de la feature DANS la worktree : pose `.claude/settings.local.json` (gitignoré) —
    # hooks/permissions du type de travail. Idempotent (overwrite). La persona/méthode passent, elles, par
    # le prompt (`build_worker_prompt`). Fail-soft si la facette n'a pas de settings.local.json.
    facet_mod.activate_facet(wt, facet_mod.resolve_facet(wt, feat.get("facet")))
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


def audit(conn: sqlite3.Connection, settings: Settings, git: GitBackend) -> list[dict]:
    """Audit d'orphelins (doit rester à 0 après merge/reset) : port réservé sans worktree sur disque,
    worktree sur disque sans réservation de port, **ou worktree sur base périmée** (`dev` n'est plus ancêtre
    de `feature/<x>` — un run interrompu avant l'avancée de `dev`, que `reserve` ne réaligne qu'au prochain
    passage : ici la détection est **proactive**). Retourne la liste des anomalies `{kind, project, …}`."""
    orphans: list[dict] = []
    reserved = ports.list_reservations(conn)
    reserved_keys = {(r["project"], r["purpose"]) for r in reserved}
    for r in reserved:
        feature = r["purpose"].removeprefix("worktree:")
        wt = worktree_path_for(settings, r["project"], feature)
        if not wt.exists():
            orphans.append({"kind": "port-sans-worktree", "project": r["project"],
                            "feature": feature, "port": r["port"]})
    # worktrees sur disque : réservation de port manquante et/ou base divergée de `dev`
    for proj_dir in _iter_worktree_dirs(settings):
        project, feature = proj_dir
        if (project, _purpose(feature)) not in reserved_keys:
            orphans.append({"kind": "worktree-sans-port", "project": project, "feature": feature})
        try:
            feat = model.resolve_feature(conn, f"{project}/{feature}")
        except KeyError:
            continue                                  # worktree sans feature DB → déjà couvert (sans-port)
        sot = sot_path_for(settings, project)
        if not git.is_ancestor(sot, WORKTREE_BASE, feat["branch"]):
            orphans.append({"kind": "worktree-base-perimee", "project": project, "feature": feature})
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
