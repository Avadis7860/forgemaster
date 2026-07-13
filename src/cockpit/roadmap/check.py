"""check — gate de **complétude** d'une roadmap : une roadmap est *opérationnelle* (drainable par
l'orchestrateur) ssi chaque task porte une DoD, chaque dépendance existe, aucun cycle, et chaque feature
cible une facette connue du bundle du projet.

Réutilise l'**autorité de séquencement** (`resolver.classify` : dangling → ERROR, cycle → CYCLE) — zéro
réécriture du DAG — et le vocab de facettes registre-driven (`model._project_facets`). Déterministe,
read-only ; **sémantique de gate** (exit 1 dès une issue). C'est l'unique autorité de complétude, partagée
par le chemin CLI et l'API (aucune contrainte n'est durcie côté `model`/HTTP, qui restent souples).
"""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass

from cockpit.config import Settings
from cockpit.db import store
from cockpit.projects.registry import get_project
from cockpit.roadmap import model, resolver


@dataclass(frozen=True)
class Issue:
    """Un défaut de complétude localisé (feature, éventuellement task) avec son motif lisible."""
    kind: str            # DANGLING_DEP | CYCLE | MISSING_ACCEPTANCE | BAD_FACET | MISSING_FACET | EMPTY
    feature: str
    task: str | None
    detail: str


def check_roadmap(conn: sqlite3.Connection, project_slug: str) -> list[Issue]:
    """Liste les défauts qui empêchent une roadmap d'être opérationnelle (liste vide = drainable).
    Read-only ; lève `KeyError`/`ValueError` si le projet est inconnu."""
    project = get_project(conn, project_slug)
    valid_facets = model._project_facets(project)
    features = model.list_features(conn, project_slug)
    if not features:
        return [Issue("EMPTY", "-", None, f"projet {project_slug} : aucune feature")]
    issues: list[Issue] = []
    for f in features:
        fslug = f["slug"]
        facet = f.get("facet")           # facette de dispatch : explicite ET connue du bundle
        if facet is None:
            issues.append(Issue("MISSING_FACET", fslug, None,
                                "feature sans facette (dispatch non aligné sur une étape)"))
        elif facet not in valid_facets:
            issues.append(Issue("BAD_FACET", fslug, None,
                                f"facette {facet!r} hors vocab du bundle : {sorted(valid_facets)}"))
        tasks = model.list_tasks(conn, f["id"])
        if not tasks:
            issues.append(Issue("EMPTY", fslug, None, "feature sans task"))
            continue
        classified = resolver.classify({t["slug"]: t for t in tasks})
        for slug, t in classified.items():
            if t["state"] == "ERROR":
                issues.append(Issue("DANGLING_DEP", fslug, slug,
                                    f"dépendance inexistante : {', '.join(t['blockers'])}"))
            elif t["state"] == "CYCLE":
                issues.append(Issue("CYCLE", fslug, slug, "task dans un cycle de dépendances"))
            if not (t.get("acceptance") or "").strip():
                issues.append(Issue("MISSING_ACCEPTANCE", fslug, slug,
                                    "task sans critère de DoD (acceptance)"))
    return issues


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit roadmap check <project>`. Rapport groupé par feature ; **exit 1 dès une issue**."""
    conn = store.open_db(settings)
    try:
        issues = check_roadmap(conn, args.project)
        feats = model.list_features(conn, args.project)
        n_feat = len(feats)
        n_task = sum(len(model.list_tasks(conn, f["id"])) for f in feats)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    if not issues:
        print(f"roadmap opérationnelle : {n_feat} features, {n_task} tasks — 0 issue")
        return 0
    print(f"roadmap NON opérationnelle : {len(issues)} issue(s)")
    for iss in issues:
        loc = f"{iss.feature}/{iss.task}" if iss.task else iss.feature
        print(f"  [{iss.kind}] {loc} — {iss.detail}")
    return 1
