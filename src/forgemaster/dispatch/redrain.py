"""redrain — recouvrement **first-class** d'une feature dont la base est PÉRIMÉE non-réalignable.

Quand plusieurs features sibling éditent les mêmes fichiers FONDATIONNELS et qu'un merge fait avancer `dev`,
le rebase de la feature suivante conflit non-trivialement : `worktree.reserve → rebase_onto` fait `rebase
--abort` puis lève `GitOpError` (jamais d'écrasement silencieux). Le message « re-drainer la feature » a
désormais une RECETTE supportée — au lieu d'un reset git manuel du SoT (band-aid interdit).

Mécanique (réutilise l'existant, aucune primitive git neuve) : `worktree.release` retire le worktree +
relâche le port (idempotent) → la branche `feature/<slug>` sera **réinitialisée sur `dev` au prochain
`reserve`** (`add_worktree -B <branch> <path> dev` reset la ref, cf. `git/internal.add_worktree`) → toutes
les tasks non-annulées repassent `todo` (le résolveur les re-voit READY) → `features.status='planned'`. Un
`dispatch` suivant re-draine depuis le `dev` COURANT (fondations héritées du sibling mergé → plus de conflit).

Invariants : **idempotent** ; `dev`/`main` JAMAIS touchés ; zéro orphelin après (`worktree.audit` : le
release relâche worktree ET port ensemble). Aucun spawn `claude` — mutation locale pure (comme `abort`).
"""
from __future__ import annotations

import argparse
import sqlite3

from forgemaster.config import Settings
from forgemaster.db import alerts, store
from forgemaster.dispatch import worktree as worktree_mod
from forgemaster.git.backend import GitBackend
from forgemaster.git.internal import InternalGit
from forgemaster.roadmap import model

# Alertes stale à résoudre au re-drain : la feature repart au drain → son blocage worker-level et son gate
# rouge sont ré-évalués (l'alerte ré-émet si le conflit / le rouge persiste après re-drain).
_STALE_KINDS = ("worker_failed", "rate_limited", "interrupted", "gate_red")


def redrain_feature(conn: sqlite3.Connection, settings: Settings, *, project: str, feature: str,
                    git: GitBackend | None = None) -> dict:
    """Re-draine `project/feature` : retire son worktree (branche réinitialisée sur `dev` au prochain
    `reserve` via `add_worktree -B`), remet toutes ses tasks non-annulées `todo`, repasse la feature
    `planned`, résout ses alertes stale. **Idempotent** ; ne merge/ne pousse rien ; `dev`/`main` intouchés.
    `KeyError` si la feature est absente (→ 404 côté route). Retour = compte-rendu (contrat front)."""
    git = git or InternalGit()
    feature_ref = f"{project}/{feature}"
    feat = model.resolve_feature(conn, feature_ref)   # KeyError si absent → 404
    worktree_mod.release(conn, settings, git, project=project, feature=feature)   # worktree + port (idempot.)
    n_reset = conn.execute(
        "UPDATE tasks SET status = 'todo' WHERE feature_id = ? AND status != 'cancelled'",
        (feat["id"],)).rowcount
    conn.execute("UPDATE features SET status = 'planned' WHERE id = ?", (feat["id"],))
    conn.commit()
    alerts.resolve_alerts(conn, project=project, feature_ref=feature_ref, kinds=_STALE_KINDS)
    return {"project": project, "feature": feature, "redrained": True, "tasks_reset": n_reset,
            "next_step": f"re-dispatch : la feature repart de `dev` (fondations à jour) — "
                         f"`forgemaster dispatch {project} {feature}` ou POST /api/dispatch/{feature_ref}"}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """`forgemaster redrain <project> <feature>` — recouvrement d'une feature à base périmée
    non-réalignable."""
    conn = store.open_db(settings)
    try:
        try:
            report = redrain_feature(conn, settings, project=args.project, feature=args.feature)
        except KeyError:
            print(f"erreur : feature {args.project}/{args.feature} introuvable")
            return 1
    finally:
        conn.close()
    print(f"✓ {report['project']}/{report['feature']} re-drainée — {report['tasks_reset']} task(s) → todo, "
          f"worktree purgé (branche réinitialisée sur dev au prochain dispatch).")
    print(f"  → {report['next_step']}")
    return 0
