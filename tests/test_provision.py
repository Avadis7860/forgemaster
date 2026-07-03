"""Tests de provision — les bundles vendorés (base ⊕ overlay) chargés en mapping chemin→contenu."""
from __future__ import annotations

import tomllib

import pytest

from cockpit.provision import BUNDLE_TYPES, load_bundle, load_payload

# Fichiers-clés attendus dans le payload (dont dotfiles / dossiers cachés).
_EXPECTED = (
    "CLAUDE.md",
    ".gitignore",
    ".docsmap.toml",
    ".codemap.toml",
    ".frontmap.toml",
    "docs/architecture.md",
    ".claude/settings.json",
    ".claude/skills/work-loop/SKILL.md",
    ".claude/skills/quality-gate/SKILL.md",
)


def test_load_payload_has_toolkit_and_dotfiles():
    payload = load_payload()
    # Garde anti-régression : `.docsmap.toml` et `.claude/` sont des dotfiles — `iterdir()` DOIT les inclure
    # (un glob `*` shell-like les raterait). Chaque fichier-clé présent et non vide.
    for key in _EXPECTED:
        assert key in payload, f"payload manque {key}"
        assert payload[key].strip(), f"payload {key} vide"


def test_payload_is_generic_and_points_docsmap():
    payload = load_payload()
    # Générique : le CLAUDE.md semé oriente vers `docsmap where` (le levier) et n'a aucun slug en dur.
    assert "docsmap where" in payload["CLAUDE.md"]
    assert "work-loop" in payload[".claude/skills/work-loop/SKILL.md"]


def test_load_payload_is_deterministic():
    # Deux lectures → mapping identique (ordre trié, lecture pure de fichiers vendorés).
    assert load_payload() == load_payload()


# -- v6 (typed-bundles) : base ⊕ overlay ------------------------------------------------------------

_OVERLAY_TYPES = ("service-api", "cli-tool", "front-ts")


def test_generic_equals_base_equals_load_payload():
    # `generic` = base seule ; `load_payload` est le shim de compat. Les trois coïncident exactement.
    assert load_bundle("generic") == load_bundle() == load_payload()


def test_load_bundle_rejects_unknown_type():
    with pytest.raises(ValueError, match="type de projet invalide"):
        load_bundle("rust-cli")


def test_load_bundle_is_deterministic():
    for t in BUNDLE_TYPES:
        assert load_bundle(t) == load_bundle(t)     # lecture triée + merge `|` déterministe


def test_overlay_adds_facets_and_overrides_whole_file():
    base = load_bundle("generic")
    svc = load_bundle("service-api")
    # (a) l'overlay AJOUTE sa facette (fichier overlay-only)
    assert ".claude/facets/backend/PERSONA.md" in svc
    assert ".claude/facets/backend/PERSONA.md" not in base
    # (b) la facette `doc` de base est CONSERVÉE (union, pas remplacement de dossier)
    assert ".claude/facets/doc/PERSONA.md" in svc
    # (c) SURCHARGE whole-file : docs/architecture.md et .cockpit/bundle.toml diffèrent de base
    assert svc["docs/architecture.md"] != base["docs/architecture.md"]
    assert "service / API" in svc["docs/architecture.md"]
    assert 'project_type = "service-api"' in svc[".cockpit/bundle.toml"]
    # (d) le contrat commun (CLAUDE.md, skills) reste celui de base (non dupliqué par l'overlay)
    assert svc["CLAUDE.md"] == base["CLAUDE.md"]
    assert ".claude/skills/work-loop/SKILL.md" in svc


@pytest.mark.parametrize("project_type", _OVERLAY_TYPES)
def test_declared_facets_have_backing_dirs(project_type):
    """Cohérence : chaque facette déclarée dans `.cockpit/bundle.toml` a un dossier `.claude/facets/<f>/`
    avec PERSONA.md/METHOD.md/settings.local.json dans le bundle composé. `default_facet` ∈ facets."""
    bundle = load_bundle(project_type)
    manifest = tomllib.loads(bundle[".cockpit/bundle.toml"])["bundle"]
    assert manifest["project_type"] == project_type
    assert manifest["default_facet"] in manifest["facets"]
    for facet in manifest["facets"]:
        for leaf in ("PERSONA.md", "METHOD.md", "settings.local.json"):
            key = f".claude/facets/{facet}/{leaf}"
            assert key in bundle, f"{project_type} : facette {facet} déclarée sans {key}"
            assert bundle[key].strip(), f"{project_type} : {key} vide"


def test_facet_settings_local_are_seeded_files_not_ignored():
    # Les settings.local.json SOURCES vivent sous .claude/facets/**/ → vendorés (le walk les inclut).
    # Le .gitignore de base n'ignore que la COPIE activée `.claude/settings.local.json` (enfant direct) :
    # le motif `.claude/*.local.json` ne traverse pas `/`, donc les sources nichées survivent.
    svc = load_bundle("service-api")
    assert ".claude/facets/backend/settings.local.json" in svc
    gitignore = svc[".gitignore"]
    assert ".claude/settings.local.json" in gitignore        # la copie activée (Phase 3) est ignorée
    assert ".claude/facets/" not in gitignore                # les sources de facette ne sont PAS ignorées
