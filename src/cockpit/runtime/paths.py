"""paths — helpers **purs** du runtime : nom de compose-project (= namespace d'isolation) et répertoire de
build/run par (projet, branche). Aucun effet de bord, aucune I/O — juste des chemins déterministes (testables
sans DB ni conteneur)."""
from __future__ import annotations

from pathlib import Path

from cockpit.config import Settings


def compose_project_name(slug: str, branch: str) -> str:
    """Nom du compose-project d'un déploiement = `cockpit-<slug>-<branch>`. **C'est la frontière
    d'isolation** : passé en `compose -p <name>`, il préfixe conteneurs/réseaux/volumes → deux projets (ou
    deux branches) ne partagent jamais un namespace (anti-pollution par construction, cf. runtime/)."""
    return f"cockpit-{slug}-{branch}"


def deploy_dir_for(settings: Settings, slug: str, branch: str) -> Path:
    """Répertoire de build/run d'un déploiement : `<projects_root>/<slug>/deploy/<branch>/`. Y est extrait
    l'arbre de la réf (via `git archive`) — le `compose.yaml` y est attendu à la racine (semé par P3). Voisin
    du SoT bare (`<projects_root>/<slug>/sot.git`), jamais mélangé avec lui."""
    return settings.projects_root / slug / "deploy" / branch
