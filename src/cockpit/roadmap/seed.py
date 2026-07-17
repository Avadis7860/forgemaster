"""seed — traduit la graine `.cockpit/launch-roadmap.yaml` d'un type de bundle en rows features/tasks du
board (DB) d'un projet fraîchement créé. **Pure seed** : aucune logique métier, aucune heuristique
design-first dans le moteur — l'ordre de lancement naît de la GRAINE (le DAG `depends_on` semé), jamais du
resolver. C'est le maillon « roadmap de lancement » du pipeline (cf. tasks/cockpit-launch-roadmap-seed).

Appelé par `projects.registry.create_project` sur le chemin SEED seulement (un projet **adopté** garde son
propre historique et n'est jamais semé). Import **paresseux** côté registry (évite le cycle registry↔model).
"""
from __future__ import annotations

import sqlite3

from cockpit.provision import load_launch_roadmap
from cockpit.roadmap import model


def seed_launch_roadmap(conn: sqlite3.Connection, *, project_slug: str, project_type: str) -> int:
    """Sème la roadmap de lancement du `project_type` dans le board de `project_slug`. Retourne le nombre de
    features semées (0 si le type ne porte aucune graine — fail-soft de `load_launch_roadmap`). Idempotence :
    aucune sur un projet neuf ; `add_feature`/`add_task` lèvent `ValueError` sur doublon (l'appelant décide
    quoi en faire — cf. create_project qui enveloppe fail-soft, jamais de rollback de la row/SoT)."""
    spec = load_launch_roadmap(project_type)
    features = spec.get("features", [])
    for feat in features:
        model.add_feature(conn, project_slug=project_slug, slug=feat["slug"],
                          title=feat.get("title"), facet=feat.get("facet"),
                          depends_on=feat.get("depends_on") or [])   # v10 : DAG inter-feature semable
        for task in feat.get("tasks", []):
            model.add_task(conn, feature_ref=f"{project_slug}/{feat['slug']}", slug=task["slug"],
                           title=task.get("title"), depends_on=task.get("depends_on") or [],
                           priority=task.get("priority", "P1"), acceptance=task.get("acceptance"),
                           mode=task.get("mode", "headless"))   # v12 : le hint interactif naît de la graine
    return len(features)
