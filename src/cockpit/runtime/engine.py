"""engine — la couche **policy** du runtime : orchestre git + ports + deployments + le backend compose pour
piloter le cycle de vie d'un déploiement (calque `dispatch/orchestrator.py`). Chaque op :
1. résout le projet (`get_project` → `KeyError`/404) et le déploiement (P1) ;
2. matérialise le contexte de build depuis le SoT **bare** (`git archive` — read-only, sans effet de bord
   sur les refs) ;
3. réserve un port de service dans un **pool deploy distinct** (jamais de collision worktree↔deploy) ;
4. pilote le backend (`up`/`down`/`restart`/`ps`) sur le compose-project (= namespace d'isolation) ;
5. écrit les transitions d'état via `set_deployment` (`building→running`, `→stopped`, `→unhealthy`).

Un échec dur du moteur (`ComposeError`) est converti en `ValueError` (→ 400 route / `erreur` CLI) — jamais
un 500 opaque ni un faux-vert.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

from cockpit.config import Settings
from cockpit.db import store
from cockpit.dispatch import ports
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects.deployments import get_deployment, set_deployment
from cockpit.projects.registry import get_project
from cockpit.runtime.backend import ComposeBackend, ComposeError, PodmanCompose, is_running
from cockpit.runtime.paths import compose_project_name, deploy_dir_for

_BRANCHES = ("main", "dev")
# pool de ports **deploy**, DISTINCT du pool worktree (5170-5249) → jamais de collision worktree↔deploy.
DEPLOY_RANGE: tuple[int, int] = (5250, 5329)


def _deploy_purpose(branch: str) -> str:
    """Clé de réservation de port d'un déploiement — distincte de `worktree:<feature>` (broker mono-hôte)."""
    return f"deploy:{branch}"


def _resolve(conn: sqlite3.Connection, slug: str, branch: str) -> dict:
    """Résout le projet + valide la branche (fail-loud avant tout effet). `KeyError` projet absent → 404."""
    if branch not in _BRANCHES:
        raise ValueError(f"branche invalide : {branch!r} (attendu {' | '.join(_BRANCHES)})")
    return get_project(conn, slug)


def deploy(conn: sqlite3.Connection, settings: Settings, *, slug: str, branch: str,
           git: InternalGit | None = None, backend: ComposeBackend | None = None) -> dict:
    """Déploie (ou re-déploie) le service de `(slug, branch)` : `building` → extrait l'arbre de la réf →
    réserve le port de service → `compose up -d --build` → `running` (+ port, url, sha, compose_ref). Un échec
    de build/run pose `unhealthy` et lève `ValueError`. Idempotent sur le port (même (slug, deploy:branch) →
    même port au re-deploy)."""
    proj = _resolve(conn, slug, branch)
    project_id = str(proj["id"])
    git = git or InternalGit()
    backend = backend or PodmanCompose(cmd=settings.compose_cmd)
    name = compose_project_name(slug, branch)
    workdir = deploy_dir_for(settings, slug, branch)

    set_deployment(conn, project_id, branch, status="building", compose_ref=name)

    # Contexte de build : arbre de la réf extrait à neuf (snapshot read-only du SoT, aucun worktree/ref muté).
    if workdir.exists():
        shutil.rmtree(workdir)
    try:
        git.archive(Path(proj["sot_path"]), branch, workdir)
        sha = git.feature_sha(Path(proj["sot_path"]), branch)
    except GitOpError as exc:
        set_deployment(conn, project_id, branch, status="unhealthy")
        raise ValueError(f"contexte de build indisponible ({branch}) : {exc}") from exc

    res = ports.reserve(conn, project=slug, purpose=_deploy_purpose(branch),
                        port_range=DEPLOY_RANGE, probe=ports.local_probe)
    port = int(res["port"])
    # Le compose publie `${COCKPIT_PORT}` (contrat semé par P3) ; on l'injecte + le nom de projet dans l'env.
    env = {"COCKPIT_PORT": str(port), "COMPOSE_PROJECT_NAME": name}
    try:
        backend.up(name, workdir, env=env)
    except ComposeError as exc:
        set_deployment(conn, project_id, branch, status="unhealthy")
        raise ValueError(f"deploy échoué ({slug}/{branch}) : {exc}") from exc

    return set_deployment(conn, project_id, branch, status="running", port=port,
                          url=f"http://127.0.0.1:{port}", last_deploy_sha=sha, compose_ref=name)


def stop(conn: sqlite3.Connection, settings: Settings, *, slug: str, branch: str,
         backend: ComposeBackend | None = None) -> dict:
    """Arrête le service (`compose down` : conteneurs + réseau retirés, namespace nettoyé) → `stopped`. Le
    **port reste réservé** (URL stable, re-`up` idempotent) ; il n'est relâché qu'à la destruction du projet
    (hors P2). Un `down` sur un déploiement jamais monté est un no-op honnête (`no_deploy` conservé)."""
    proj = _resolve(conn, slug, branch)
    project_id = str(proj["id"])
    backend = backend or PodmanCompose(cmd=settings.compose_cmd)
    name = compose_project_name(slug, branch)
    workdir = deploy_dir_for(settings, slug, branch)

    dep = get_deployment(conn, project_id, branch)
    if dep["status"] == "no_deploy" or not workdir.exists():
        return dep                                        # rien à arrêter — vide honnête
    try:
        backend.down(name, workdir, env={"COMPOSE_PROJECT_NAME": name})
    except ComposeError as exc:
        raise ValueError(f"stop échoué ({slug}/{branch}) : {exc}") from exc
    return set_deployment(conn, project_id, branch, status="stopped")


def restart(conn: sqlite3.Connection, settings: Settings, *, slug: str, branch: str,
            backend: ComposeBackend | None = None) -> dict:
    """Redémarre les conteneurs du déploiement (`compose restart`) → `running`. Opère sur un déploiement
    **monté** (après un `down`, ré-`up`). Un échec pose `unhealthy` et lève `ValueError`."""
    proj = _resolve(conn, slug, branch)
    project_id = str(proj["id"])
    backend = backend or PodmanCompose(cmd=settings.compose_cmd)
    name = compose_project_name(slug, branch)
    workdir = deploy_dir_for(settings, slug, branch)
    try:
        backend.restart(name, workdir, env={"COMPOSE_PROJECT_NAME": name})
    except ComposeError as exc:
        set_deployment(conn, project_id, branch, status="unhealthy")
        raise ValueError(f"restart échoué ({slug}/{branch}) : {exc}") from exc
    return set_deployment(conn, project_id, branch, status="running")


def status(conn: sqlite3.Connection, settings: Settings, *, slug: str, branch: str,
           backend: ComposeBackend | None = None) -> dict:
    """Réconcilie le status DB avec l'état **live** des conteneurs (`compose ps`) — read-only, idempotent.
    `running` si au moins un conteneur tourne, sinon `stopped` ; `ps` en échec → `unhealthy`. Un déploiement
    jamais monté (`no_deploy` ou aucun workdir) est rendu tel quel (jamais un faux-vert)."""
    proj = _resolve(conn, slug, branch)
    project_id = str(proj["id"])
    backend = backend or PodmanCompose(cmd=settings.compose_cmd)
    name = compose_project_name(slug, branch)
    workdir = deploy_dir_for(settings, slug, branch)

    dep = get_deployment(conn, project_id, branch)
    if dep["status"] == "no_deploy" or not workdir.exists():
        return dep
    try:
        rows = backend.ps(name, workdir, env={"COMPOSE_PROJECT_NAME": name})
    except ComposeError:
        return set_deployment(conn, project_id, branch, status="unhealthy")
    live = "running" if any(is_running(r) for r in rows) else "stopped"
    return set_deployment(conn, project_id, branch, status=live)


_ACTIONS = {"up": deploy, "down": stop, "restart": restart, "status": status}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit deploy <up|down|restart|status> <slug> <branch>`. Corps canonique (open_db, branch sur
    `args.action`, `(ValueError,KeyError) → erreur/1`, `finally close`)."""
    conn = store.open_db(settings)
    try:
        dep = _ACTIONS[args.action](conn, settings, slug=args.slug, branch=args.branch)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    tail = f" → {dep['url']}" if dep.get("url") else ""
    print(f"{args.slug}/{args.branch} : {dep['status']}{tail}")
    return 0
