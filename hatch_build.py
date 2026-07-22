"""hatch_build.py — hook de packaging : embarque dans le wheel les artefacts sibling que la cible
Python-seule ne pourrait pas se procurer autrement — la **SPA** buildée (`cockpit/_web_dist`), le package
**`codemap`** (l'outil code-map, `python -m codemap`) ET le package **`taskmap`** (moteur de graphe importé
par `roadmap/resolver.py` ; vendoré → wheel auto-contenu, aucune dép git privée à cloner au `pip install`).

Distribution turnkey : **l'utilisateur final n'installe que Python** ; l'UI *et* code-map voyagent dans le
wheel. Le hook est **volontairement minimal** — il *force-include* ce qui est déjà stagé, il ne build/ne
copie rien lui-même :
  • builder le front est le rôle de `cockpit setup` (from-clone) ou de la CI (`npm run build`) **avant** de
    packager ; stager code-map est le rôle de `deploy/build-wheel.sh` (copie le sibling `../code-map`) ;
  • agir dans le hook le ferait tourner aussi sur `pip install -e` (l'éditable passe par la cible `wheel`)
    → footgun sur chaque install dev. On l'évite : en dev, la SPA vient de `cockpit setup` et code-map est
    déjà éditable-installé (sibling), donc les deux stagings sont absents → skip propre.
Chaque artefact absent → warning clair + wheel dégradé (SPA : `cockpit setup` la posera ; codemap : Flow
indisponible tant que non stagé). Ne re-committe jamais ces artefacts dans git.
"""
from __future__ import annotations

from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ModuleNotFoundError:                              # hatchling n'existe qu'au build (env isolé) ; hors
    BuildHookInterface = object                          # de là on n'importe QUE la logique pure (tests).

# Messages d'absence (constantes → testables sans instancier le hook).
_WARN_NO_SPA = (
    "[cockpit] web/dist absente → wheel SANS UI. Lance `npm run build` (ou `cockpit setup`) "
    "avant de packager pour embarquer l'interface."
)
_WARN_NO_CODEMAP = (
    "[cockpit] build/vendor/codemap absent → wheel SANS code-map (Flow indisponible côté cible). "
    "Passe par `deploy/build-wheel.sh` (il stage le sibling ../code-map) pour un wheel de release."
)
_WARN_NO_TASKMAP = (
    "[cockpit] build/vendor/taskmap absent → wheel SANS taskmap (moteur DAG ABSENT côté cible → "
    "`No module named taskmap`). Passe par `deploy/build-wheel.sh` (il stage le sibling ../task-map)."
)
_WARN_NO_RUNNER = (
    "[cockpit] build/vendor/verify-runner absent → wheel SANS runner Tier-1.5 (`cockpit gate verify` "
    "fail-close côté cible). Passe par `deploy/build-wheel.sh` (il stage deploy/runners)."
)


def plan_force_includes(root: Path) -> tuple[dict[str, str], list[str]]:
    """Décision **pure** (testable, sans hatchling) : quels artefacts sibling embarquer dans le wheel.
    Retourne `(force_include, warnings)`.
      • SPA buildée `web/dist` → `cockpit/_web_dist` (l'UI voyage dans le wheel) ;
      • package `build/vendor/codemap` (stagé par `deploy/build-wheel.sh`) → top-level `codemap`, importable
        par le venv (`sys.executable -m codemap`, cf. src/cockpit/codemap/index.py) — sans lui l'onglet Flow
        tombe (`No module named codemap`) ;
      • package `build/vendor/taskmap` (stagé par `deploy/build-wheel.sh`) → top-level `taskmap`, importé par
        `roadmap/resolver.py` (`taskmap.core`) — vendoré pour que le wheel soit auto-contenu (plus de dép git
        privée `task-map @ git+…` à cloner au `pip install`) ; sans lui le daemon tombe
        (`No module named taskmap`).
    Chaque artefact absent → un warning (dégradation gracieuse ; en dev/editable les stagings sont absents et
    l'on skippe proprement : la SPA vient de `cockpit setup`, code-map/task-map sont déjà
    éditable-installés)."""
    root = Path(root)
    force: dict[str, str] = {}
    warnings: list[str] = []
    if (root / "web" / "dist" / "index.html").is_file():
        force["web/dist"] = "cockpit/_web_dist"
    else:
        warnings.append(_WARN_NO_SPA)
    if (root / "build" / "vendor" / "codemap" / "__main__.py").is_file():
        force["build/vendor/codemap"] = "codemap"
    else:
        warnings.append(_WARN_NO_CODEMAP)
    if (root / "build" / "vendor" / "taskmap" / "__init__.py").is_file():
        force["build/vendor/taskmap"] = "taskmap"
    else:
        warnings.append(_WARN_NO_TASKMAP)
    # runner Playwright du gate Tier-1.5 (`deploy/runners`, vit DANS le repo) → `cockpit/_verify_runner` :
    # provision-ct.sh le tire du package installé (wheel = artefact unique, provision-ct.sh voyage seul).
    if (root / "build" / "vendor" / "verify-runner" / "render_check.js").is_file():
        force["build/vendor/verify-runner"] = "cockpit/_verify_runner"
    else:
        warnings.append(_WARN_NO_RUNNER)
    return force, warnings


class CockpitFrontBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":                  # sdist reste source-only ; seul le wheel embarque
            return
        force, warnings = plan_force_includes(Path(self.root))
        build_data.setdefault("force_include", {}).update(force)
        for warning in warnings:
            self.app.display_warning(warning)
