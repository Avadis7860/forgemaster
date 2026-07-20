"""provision — policy de provisioning : le « Toolkit auto-travaillable » semé dans le SoT de tout projet
créé par le cockpit. Le CONTENU (bundles génériques, zéro spécifique-projet) est **vendoré** sous
`bundles/` ; ce module le compose en mapping `chemin-relatif → contenu`.

**Système de bundles par type (typed-bundles).** Un bundle = `base ⊕ overlay(type)` :
- `bundles/base/` — la couche commune (CLAUDE.md socle, skills work-loop/quality-gate, configs cartes,
  `.cockpit/bundle.toml`, facette `doc`) semée dans TOUT projet ;
- `bundles/types/<type>/` — un overlay par type qui **ajoute ou surcharge** des fichiers (persona, doc
  pré-optimisée, facettes spécifiques). Composition **whole-file** : un fichier de l'overlay remplace
  intégralement son homologue de base (`base_map | overlay_map`) ; les fichiers overlay-only s'ajoutent.

Le REGISTRE des types est le **filesystem** (`discover_types`) : déposer `bundles/types/<type>/` suffit à
rendre un type créable — aucun enum en dur, aucune migration DB (spec bundle-storage-registry). La policy
(quel bundle semer) vit ici, PAS dans `git/internal.py` (qui reste primitives git seules) :
`projects.registry.create_project` **valide** (`validate_bundle`, fail-closed) puis lit `load_bundle(type)`
et le passe à `InternalGit().init_sot`. Déterministe : lecture de fichiers vendorés, ordre trié, `|`
déterministe, zéro I/O réseau, zéro LLM.
"""
from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import yaml

_BUNDLES_DIR = Path(__file__).parent / "bundles"
_MANIFEST_PATH = ".cockpit/bundle.toml"   # descripteur [bundle], vendoré, lu au dispatch (facet.py)
_LAUNCH_ROADMAP_PATH = ".cockpit/launch-roadmap.yaml"   # graine de roadmap de lancement (seed au create)


class BundleError(ValueError):
    """Un bundle (type de projet) est absent du registre, ou son manifeste `.cockpit/bundle.toml` est
    invalide. Sous-classe de `ValueError` → routée en 400 (API) / message CLI comme les autres erreurs
    d'entrée, sans handler dédié."""


def discover_types() -> tuple[str, ...]:
    """Le REGISTRE des types de projet, dérivé du **filesystem** : `generic` (base seule) plus chaque
    sous-dossier de `bundles/types/`. Trié, déterministe, `generic` en tête. Ajouter un type = déposer
    `bundles/types/<type>/` — zéro code, zéro migration DB."""
    types_dir = _BUNDLES_DIR / "types"
    overlays = sorted(d.name for d in types_dir.iterdir() if d.is_dir()) if types_dir.is_dir() else []
    return ("generic", *overlays)


# Dossiers d'artefacts/caches JAMAIS semés : sources only. Un cache d'outil (`.ruff_cache`, `.mypy_cache`,
# `node_modules`…) porte des fichiers **binaires** qui casseraient la lecture UTF-8 du payload ET pollueraient
# le SoT d'un projet. Ces caches peuvent apparaître dans un bundle si un outil tourne sur son manifeste
# (ex. `ruff` descend dans un `pyproject.toml` de seed) → on les ignore à la lecture, par principe.
_SKIP_DIRS = frozenset({
    "__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache", "node_modules", ".git",
})


def _walk_files(base: Path) -> Iterator[Path]:
    """Parcourt récursivement `base`, ordre **trié** par nom à chaque niveau (déterministe). `iterdir()`
    inclut les dotfiles (`.docsmap.toml`, `.claude/`, `.cockpit/`) — voulu. Les artefacts de compilation et
    caches d'outils (`_SKIP_DIRS`, `*.pyc`) sont **exclus** : un payload semé porte des SOURCES, jamais du
    binaire (qui casserait la lecture UTF-8 et polluerait le SoT d'un projet)."""
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if entry.name in _SKIP_DIRS:
            continue
        if entry.is_dir():
            yield from _walk_files(entry)
        elif entry.is_file() and entry.suffix != ".pyc":
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
    les siennes). Déterministe. Lève `BundleError` si `project_type` n'est pas dans le registre
    (`discover_types`)."""
    if project_type not in discover_types():
        raise BundleError(
            f"type de projet inconnu : {project_type!r} (attendu {' | '.join(discover_types())})")
    files = _read_tree(_BUNDLES_DIR / "base")
    if project_type != "generic":
        files |= _read_tree(_BUNDLES_DIR / "types" / project_type)
    return files


def _parse_manifest(files: dict[str, str], project_type: str) -> dict:
    """La table `[bundle]` du manifeste `.cockpit/bundle.toml` d'un bundle composé. Lève `BundleError`
    si le manifeste est absent ou illisible."""
    raw = files.get(_MANIFEST_PATH)
    if raw is None:
        raise BundleError(f"bundle {project_type!r} : manifeste {_MANIFEST_PATH} absent")
    try:
        return tomllib.loads(raw).get("bundle", {})
    except tomllib.TOMLDecodeError as exc:
        raise BundleError(f"bundle {project_type!r} : manifeste illisible ({exc})") from exc


def read_bundle_manifest(project_type: str = "generic") -> dict:
    """Le manifeste `[bundle]` (version, project_type, facets, default_facet…) du bundle composé
    `base ⊕ overlay(type)`. Point d'accès amont pour la sélection, la provenance et la gestion."""
    return _parse_manifest(load_bundle(project_type), project_type)


def validate_bundle(project_type: str = "generic") -> None:
    """Valide un bundle **avant toute copie** (fail-closed). Lève `BundleError` si : type hors registre ;
    manifeste absent/illisible ; `version` manquante ; `project_type` du manifeste ≠ nom du dossier ;
    `facets` vide ; `default_facet` ∉ `facets` ; une facette déclarée sans dossier `.claude/facets/<f>/`
    de support dans le bundle composé ; un bloc `[bundle.mcp]` présent mais mal typé (`corpus` non-booléen,
    `tech_scope` non-nom). Le bloc `mcp` est **optionnel** (absent ⇒ valide) : déclaration sèche du besoin
    de corpus, jamais un secret ni un endpoint."""
    files = load_bundle(project_type)                      # lève BundleError si type hors registre
    manifest = _parse_manifest(files, project_type)
    if not manifest.get("version"):
        raise BundleError(f"bundle {project_type!r} : `version` manquante au manifeste")
    declared = manifest.get("project_type")
    if declared != project_type:
        raise BundleError(
            f"bundle {project_type!r} : manifeste project_type={declared!r} ≠ nom du dossier")
    facets = manifest.get("facets") or []
    if not facets:
        raise BundleError(f"bundle {project_type!r} : aucune facette déclarée (`facets` vide)")
    if manifest.get("default_facet") not in facets:
        raise BundleError(
            f"bundle {project_type!r} : default_facet={manifest.get('default_facet')!r} ∉ {facets}")
    for fac in facets:
        prefix = f".claude/facets/{fac}/"
        if not any(p.startswith(prefix) for p in files):
            raise BundleError(f"bundle {project_type!r} : facette {fac!r} sans dossier {prefix}")
    mcp_decl = manifest.get("mcp")                             # bloc optionnel, sec (jamais de secret)
    if mcp_decl is not None:
        if not isinstance(mcp_decl, dict):
            raise BundleError(f"bundle {project_type!r} : `[bundle.mcp]` doit être une table")
        corpus = mcp_decl.get("corpus")
        if corpus is not None and not isinstance(corpus, bool):
            raise BundleError(f"bundle {project_type!r} : `mcp.corpus` doit être un booléen")
        scope = mcp_decl.get("tech_scope")
        if scope is not None and (not isinstance(scope, str) or not scope.strip()):
            raise BundleError(f"bundle {project_type!r} : `mcp.tech_scope` doit être un nom de silo non vide")


def list_valid_types() -> list[dict]:
    """Les types de projet **valides** (registre filesystem filtré par `validate_bundle`, fail-closed) avec
    leurs métadonnées de manifeste — LA source unique des types *offerts* : le dropdown de création (UI) ET
    le durcissement des `choices` CLI la consomment (zéro liste dupliquée). Un overlay cassé (manifeste
    absent/incohérent, facette sans dossier de support) est **silencieusement écarté** : on n'offre jamais
    un type qu'on ne saurait pas semer. Chaque entrée : `{type, version, project_type, facets,
    default_facet, mcp}` (`mcp` = déclaration sèche du manifeste, `{}` si absente). Déterministe, ordre de
    `discover_types` (generic en tête)."""
    valid: list[dict] = []
    for project_type in discover_types():
        try:
            validate_bundle(project_type)
        except BundleError:
            continue                                           # cassé → non offert (fail-closed)
        manifest = read_bundle_manifest(project_type)
        valid.append({
            "type": project_type,
            "version": manifest.get("version", ""),
            "project_type": manifest.get("project_type", project_type),
            "facets": manifest.get("facets", []),
            "default_facet": manifest.get("default_facet", ""),
            "mcp": manifest.get("mcp", {}),
        })
    return valid


def load_launch_roadmap(project_type: str = "generic") -> dict:
    """La graine de **roadmap de lancement** d'un type (`base ⊕ overlay(type)`, whole-file), parsée depuis
    `.cockpit/launch-roadmap.yaml` du bundle composé. Schéma = contrat `roadmap.yaml` SANS la clé `project:`
    (le slug est fourni au seed). Retourne `{}` **fail-soft** si le type ne porte aucune graine (aucun seed,
    jamais un crash). Le parse est **strict** (un YAML vendoré cassé = bug dev, attrapé par les tests, jamais
    avalé). Déterministe. Lève `BundleError` si `project_type` est hors registre (via `load_bundle`)."""
    raw = load_bundle(project_type).get(_LAUNCH_ROADMAP_PATH)
    if raw is None:
        return {}
    return yaml.safe_load(raw) or {}


def load_payload() -> dict[str, str]:
    """Compat : le bundle générique (base seule). Conservé pour les appelants qui ne typent pas encore."""
    return load_bundle("generic")
