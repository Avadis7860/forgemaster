"""Garde SoT-and-derive de la roadmap de lancement : la graine bundle `.forgemaster/launch-roadmap.yaml` ne
doit
jamais diverger du template SoT central vendoré (`provision/launch_templates/`). Drift-check léger
(Option C) qui tient la doctrine capital-jeton sans étendre `derive.py` — cf. décision vault
`corpus/decision/meta/2026-07-21--launch-roadmap-sot-and-derive-lifecycle.md`."""
from __future__ import annotations

from pathlib import Path

import yaml

import forgemaster.provision as provision
from forgemaster.provision import (
    check_launch_roadmap_drift,
    discover_types,
    load_launch_roadmap,
)


def test_shipped_bundles_have_no_launch_roadmap_drift() -> None:
    """Invariant de livraison : chaque type du registre est EN PHASE avec son mirror vendoré (0 drift)."""
    for t in discover_types():
        assert check_launch_roadmap_drift(t) == [], f"{t} : la graine a dérivé du template SoT vendoré"


def test_every_type_maps_to_a_mirror() -> None:
    """Tout type porteur d'une graine de lancement a un mirror vendoré résolu (jamais « indétectable »)."""
    for t in discover_types():
        if not load_launch_roadmap(t):
            continue
        canon = provision._canonical_launch_roadmap(t)
        assert canon is not None, f"{t} : graine présente sans mirror vendoré"


def test_generic_and_browser_game_use_distinct_mirrors() -> None:
    """La base et l'overlay `browser-game` (qui SURCHARGE la roadmap de lancement en socle design-first) sont
    deux artefacts distincts → deux mirrors distincts (`generic.yaml` vs `browser-game.yaml`). Un type sans
    overlay de lancement hérite, lui, du mirror `generic.yaml`."""
    generic = provision._canonical_launch_roadmap("generic")
    bg = provision._canonical_launch_roadmap("browser-game")
    assert generic is not None and bg is not None
    assert generic[0] == "generic.yaml" and bg[0] == "browser-game.yaml"
    assert generic[1] != bg[1]


def test_drift_detected_when_seed_diverges(monkeypatch, tmp_path: Path) -> None:
    """La garde ROUGIT si un mirror ne correspond plus à la graine — sinon le check ne protège rien."""
    mutated = yaml.safe_load((provision._LAUNCH_TEMPLATES_DIR / "generic.yaml").read_text())
    mutated["features"][0]["slug"] = "socle-modifie-a-la-main"          # divergence simulée
    (tmp_path / "generic.yaml").write_text(yaml.safe_dump(mutated, allow_unicode=True))
    monkeypatch.setattr(provision, "_LAUNCH_TEMPLATES_DIR", tmp_path)
    issues = check_launch_roadmap_drift("generic")
    assert issues and "diverge" in issues[0]


def test_missing_mirror_is_surfaced(monkeypatch, tmp_path: Path) -> None:
    """Une graine présente sans mirror vendoré = divergence indétectable → surfacée, jamais silencieuse."""
    monkeypatch.setattr(provision, "_LAUNCH_TEMPLATES_DIR", tmp_path)      # tmp vide → aucun mirror
    issues = check_launch_roadmap_drift("generic")
    assert issues and "indétectable" in issues[0]
