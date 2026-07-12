"""toolsync — re-synchronise un OUTIL adopté (`kind=tool`) avec son amont GitHub : **pull-only, ff
seulement** (P6). Pendant de `bootstrap` (adoption) : là où bootstrap CLONE au 1er démarrage, `toolsync`
RE-FETCH un outil déjà adopté et avance ses refs suivies (`dev`, `main`) quand l'amont a pris de l'avance —
**sans jamais pousser** (frontière read-only stricte). Dégèle un CT dont les outils sont figés au commit
d'adoption (cas vécu : les 4 outils du rail périmés).

Frontière **fail-close** : SEULS les `kind=tool` sont synchronisables. Un projet (`kind=project`, SoT
autoritatif) **refuse** la route (`NotAToolError` → 409) — sa voie de sync est la réconciliation gatée
(`reconcile`, P5), jamais ce pull-only. La symétrie est stricte des deux côtés : un projet n'entre pas ici,
un outil n'entre pas dans `reconcile`.

Fraîcheur Flow : l'index code-map est **caché par (SHA, schema)** (`codemap/index.py`) → dès que `dev`
avance, la prochaine requête Flow reconstruit sur le nouveau SHA (aucune « bust » à faire — le symptôme
« Flow périmé » venait du SoT jamais re-fetché, pas d'un cache tenace). On **pré-chauffe** best-effort après
un sync qui a bougé, pour que Flow soit chaud immédiatement — jamais bloquant : un outil sans code-map
installé → `index_refreshed=False`, honnête (le rebuild reste lazy au prochain accès Flow)."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from cockpit.codemap.index import CodemapError, ensure_index
from cockpit.config import Settings
from cockpit.db import store as db_store
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects.registry import get_project
from cockpit.secrets import cred_resolver

TRACKED_BRANCHES = ("dev", "main")


class NotAToolError(ValueError):
    """Le slug visé n'est pas un `kind=tool` — la re-sync pull-only lui est fermée (fail-close). Un projet
    se synchronise via la réconciliation gatée (`reconcile`, P5), jamais par ce pull-only descendant."""


def sync_tool(conn: sqlite3.Connection, settings: Settings, *, slug: str) -> dict:
    """Re-synchronise l'outil `slug` avec son amont (`origin`), **pull-only ff** (P6). Résout l'entité
    (`KeyError` si absente → 404 chez l'appelant), **refuse un projet** (`NotAToolError` → 409, fail-close),
    puis fetch+ff via `InternalGit.sync_tracking` sous auth **transitoire** (le `credential_ref` du repo, le
    token résolu à l'usage, jamais en argv ni persisté). Si `dev` a bougé, **pré-chauffe** best-effort Flow
    (jamais bloquant). Renvoie le rapport de `sync_tracking` enrichi de `{slug, kind, index_refreshed}`. Lève
    `GitOpError` sur op git dure (ref corrompue…) → 422 chez l'appelant."""
    proj = get_project(conn, slug)                  # KeyError entité absente → 404 (handler global)
    if proj["kind"] != "tool":
        raise NotAToolError(
            f"{slug!r} est un {proj['kind']} — la re-sync pull-only ne cible que les outils "
            "(un projet se réconcilie via la voie gatée `reconcile`)")
    sot = Path(proj["sot_path"])
    git = InternalGit(cred_resolver=cred_resolver(settings))
    report = git.sync_tracking(
        sot, remote="origin", branches=TRACKED_BRANCHES, creds_ref=proj["credential_ref"])
    index_refreshed = False
    if report["changed"]:                           # `dev` a (peut-être) avancé → pré-chauffe l'index Flow
        try:
            ensure_index(settings, slug, sot, ref="dev")
            index_refreshed = True
        except CodemapError:
            index_refreshed = False                 # best-effort : code-map absent/échec → Flow rebuild lazy
    return {"slug": slug, "kind": proj["kind"], **report, "index_refreshed": index_refreshed}


_ACTION_GLYPH = {
    "already_synced": "✓ à jour",
    "fast_forward": "↓ ff depuis l'amont",
    "local_ahead_skipped": "⚠ commits locaux (read-only : non poussés)",
    "blocked_worktree": "⛔ sortie dans un worktree actif",
    "blocked_diverged": "⛔ divergé (non-ff) — résolution manuelle",
}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit tool sync <slug>` : re-synchronise un outil adopté (pull-only ff). Affiche l'action par
    branche + l'état du pré-chauffage Flow. Code 1 si l'outil est introuvable, si le slug n'est pas un outil
    (fail-close), si le fetch a échoué (dégradé honnête `unreachable`/`no_mirror`), ou s'il reste des branches
    bloquées (non-ff / worktree). Code 0 si tout est à jour ou avancé proprement."""
    conn = db_store.open_db(settings)
    try:
        report = sync_tool(conn, settings, slug=args.slug)
    except KeyError:
        print(f"erreur : outil introuvable — {args.slug!r}")
        return 1
    except NotAToolError as exc:
        print(f"erreur : {exc}")
        return 1
    except GitOpError as exc:
        print(f"erreur : sync git impossible — {exc}")
        return 1
    finally:
        conn.close()
    if not report["fetched"]:
        print(f"  ⚠ amont injoignable ({report['state']}) — rien de synchronisé (SoT local intact)")
        return 1
    for b, act in report["actions"].items():
        print(f"  {b:6} {_ACTION_GLYPH.get(act['action'], act['action'])}")
    flow = "Flow pré-chauffé" if report["index_refreshed"] else "Flow reconstruit au prochain accès"
    changed = "avancé" if report["changed"] else "déjà à jour"
    print(f"sync {args.slug} : {changed} ({flow}).")
    return 1 if report["blocked"] else 0
