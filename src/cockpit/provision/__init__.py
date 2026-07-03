"""provision — policy de provisioning : le « Toolkit auto-travaillable » semé dans le SoT de tout projet
créé par le cockpit. Le CONTENU (bundles génériques, zéro spécifique-projet) est **vendoré** sous
`bundles/` ; ce module le compose en mapping `chemin-relatif → contenu`.

**Système de bundles par type (typed-bundles).** Un bundle = `base ⊕ overlay(type)` :
- `bundles/base/` — la couche commune (CLAUDE.md socle, skills work-loop/quality-gate, configs cartes,
  `.cockpit/bundle.toml`, facette `doc`) semée dans TOUT projet ;
- `bundles/types/<type>/` — un overlay par type qui **ajoute ou surcharge** des fichiers (persona, doc
  pré-optimisée, facettes spécifiques). Composition **whole-file** : un fichier de l'overlay remplace
  intégralement son homologue de base (`base_map | overlay_map`) ; les fichiers overlay-only s'ajoutent.

La **policy** (quel bundle semer) vit ici, PAS dans `git/internal.py` (qui reste primitives git seules) :
`projects.registry.create_project` lit `load_bundle(project_type)` et le passe à `InternalGit().init_sot`.
Déterministe : lecture de fichiers vendorés, ordre trié, `|` déterministe, zéro I/O réseau, zéro LLM.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

_BUNDLES_DIR = Path(__file__).parent / "bundles"

# Types de projet = overlays disponibles. `generic` = base seule (fallback, aucun overlay). L'enum est
# **re-validé** par `registry.create_project` (toute base) — un `CHECK` DDL le tient côté DB (base neuve).
BUNDLE_TYPES = ("generic", "service-api", "cli-tool", "front-ts")


def _walk_files(base: Path) -> Iterator[Path]:
    """Parcourt récursivement `base`, ordre **trié** par nom à chaque niveau (déterministe). `iterdir()`
    inclut les dotfiles (`.docsmap.toml`, `.claude/`, `.cockpit/`) — voulu."""
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            yield from _walk_files(entry)
        elif entry.is_file():
            yield entry


def _read_tree(root: Path) -> dict[str, str]:
    """Un arbre de fichiers en mapping `chemin-relatif-POSIX (à root) → contenu texte`. Ordre trié
    (déterministe). Répertoire absent → mapping vide (fail-soft)."""
    if not root.is_dir():
        return {}
    return {f.relative_to(root).as_posix(): f.read_text(encoding="utf-8") for f in _walk_files(root)}


def load_bundle(project_type: str = "generic") -> dict[str, str]:
    """Le bundle d'un type = `base ⊕ overlay(type)` en mapping `chemin-relatif-POSIX → contenu`. `generic`
    = base seule. Composition **whole-file** : `base | overlay` (l'overlay écrase les clés communes, ajoute
    les siennes). Déterministe. Lève `ValueError` si `project_type` hors `BUNDLE_TYPES`."""
    if project_type not in BUNDLE_TYPES:
        raise ValueError(f"type de projet invalide : {project_type!r} (attendu {' | '.join(BUNDLE_TYPES)})")
    files = _read_tree(_BUNDLES_DIR / "base")
    if project_type != "generic":
        files |= _read_tree(_BUNDLES_DIR / "types" / project_type)
    return files


def load_payload() -> dict[str, str]:
    """Compat : le bundle générique (base seule). Conservé pour les appelants qui ne typent pas encore."""
    return load_bundle("generic")
