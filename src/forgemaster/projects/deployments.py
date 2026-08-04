"""deployments — CRUD des **déploiements** d'un projet (table `deployments`, v7). Modèle d'identité
(spec project-hosting-branch-deploy-model) : un projet possède **2 déploiements par branche** — `main` (prod)
et `dev` (preview) — chacun avec son état de run, son port de service, son URL et le sha du dernier deploy.

Substrat-agnostique : ce module ne connaît QUE la donnée. Le *run* (backend conteneur compose) vit en P2
(`runtime/`), l'*observabilité* en P5 ; ils **consomment** `set_deployment`/`list_deployments`.

Reçoit une connexion (jamais un module-global, correctif anti god-module). **N'importe pas `registry`** : le
slug→id est résolu côté appelant (route/CLI) — évite le cycle registry↔deployments.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from forgemaster.core import ids

_BRANCHES = ("main", "dev")   # les 2 déploiements de tout projet : prod (main) + preview (dev)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_deployments(conn: sqlite3.Connection, project_id: str) -> None:
    """Garantit (idempotent) les 2 lignes de déploiement (`main`, `dev`) d'un projet, en `no_deploy`.
    `INSERT OR IGNORE` sur la contrainte `UNIQUE(project_id, branch)` → ré-appel sans effet, aucune
    duplication. Semé à la création du projet ET rejoué à la lecture (couvre les projets d'avant v7, dont la
    table `deployments` — neuve — part vide)."""
    now = _now()
    for branch in _BRANCHES:
        conn.execute(
            "INSERT OR IGNORE INTO deployments "
            "(id, project_id, branch, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'no_deploy', ?, ?)",
            (ids.new_id(), project_id, branch, now, now))
    conn.commit()


def list_deployments(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Les 2 déploiements d'un projet, **`main` puis `dev`** (prod d'abord). `ensure_deployments` d'abord
    (robustesse : un projet créé avant v7 obtient ses lignes à la première lecture)."""
    ensure_deployments(conn, project_id)
    # 'main' > 'dev' en lexical → ORDER BY branch DESC place prod avant preview (ordre stable, documenté).
    rows = conn.execute(
        "SELECT * FROM deployments WHERE project_id = ? ORDER BY branch DESC", (project_id,)).fetchall()
    return [dict(r) for r in rows]


def get_deployment(conn: sqlite3.Connection, project_id: str, branch: str) -> dict:
    """Un déploiement par (projet, branche), ou `KeyError` s'il n'existe pas."""
    r = conn.execute(
        "SELECT * FROM deployments WHERE project_id = ? AND branch = ?", (project_id, branch)).fetchone()
    if r is None:
        raise KeyError(f"{project_id}/{branch}")
    return dict(r)


def set_deployment(conn: sqlite3.Connection, project_id: str, branch: str, *,
                   status: str | None = None, port: int | None = None, url: str | None = None,
                   last_deploy_sha: str | None = None, compose_ref: str | None = None) -> dict:
    """Upsert **partiel** de l'état d'un déploiement : seuls les champs non-`None` sont écrits (`None` =
    inchangé), `updated_at` est toujours bumpé. Consommé par P2 (deploy) et P5 (observabilité).
    Lève `ValueError` si `branch` hors `{main,dev}`, `KeyError` si le déploiement n'existe pas ; row relue."""
    if branch not in _BRANCHES:
        raise ValueError(f"branche invalide : {branch!r} (attendu {' | '.join(_BRANCHES)})")
    provided = {"status": status, "port": port, "url": url,
                "last_deploy_sha": last_deploy_sha, "compose_ref": compose_ref}
    changed = {k: v for k, v in provided.items() if v is not None}
    assignments = [f"{col} = :{col}" for col in changed]
    assignments.append("updated_at = :updated_at")
    params: dict = {**changed, "updated_at": _now(), "project_id": project_id, "branch": branch}
    cur = conn.execute(
        f"UPDATE deployments SET {', '.join(assignments)} "  # noqa: S608 — colonnes littérales, jamais d'input
        "WHERE project_id = :project_id AND branch = :branch", params)
    if cur.rowcount == 0:
        raise KeyError(f"{project_id}/{branch}")
    conn.commit()
    return get_deployment(conn, project_id, branch)
