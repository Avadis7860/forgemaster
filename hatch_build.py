"""hatch_build.py — hook de packaging : embarque dans le wheel les artefacts sibling que la cible
Python-seule ne pourrait pas se procurer autrement — la **SPA** buildée (`forgemaster/_web_dist`), le package
**`codemap`** (l'outil code-map, `python -m codemap`), le package **`taskmap`** (moteur de graphe importé
par `roadmap/resolver.py` ; vendoré → wheel auto-contenu, aucune dép git privée à cloner au `pip install`)
ET l'**édition des 3 cartes hôte** (`forgemaster/_maps` : code-map/docs-map/front-map bâties en wheels +
leur manifeste) — sans quoi `forgemaster toolchain install` les tirerait d'une réf **mobile**.

Distribution turnkey : **l'utilisateur final n'installe que Python** ; l'UI *et* code-map voyagent dans le
wheel. Le hook est **volontairement minimal** — il *force-include* ce qui est déjà stagé, il ne build/ne
copie rien lui-même :
  • builder le front est le rôle de `forgemaster setup` (from-clone) ou de la CI (`npm run build`) **avant**
  de
    packager ; stager code-map est le rôle de `deploy/build-wheel.sh` (copie le sibling `../code-map`) ;
  • agir dans le hook le ferait tourner aussi sur `pip install -e` (l'éditable passe par la cible `wheel`)
    → footgun sur chaque install dev. On l'évite : en dev, la SPA vient de `forgemaster setup` et code-map est
    déjà éditable-installé (sibling), donc les deux stagings sont absents → skip propre.
Chaque artefact absent → warning clair + wheel dégradé (SPA : `forgemaster setup` la posera ; codemap : Flow
indisponible tant que non stagé). Ne re-committe jamais ces artefacts dans git.

Ce module porte AUSSI la garde d'**inventaire** (`unexpected_wheel_members`) : le pendant symétrique du
force-include. Le wheel a **deux portes**, et une seule filtrait — `packages = ["src/forgemaster"]` écarte ce
que le `.gitignore` écarte, `force_include` embarque le dossier **tel qu'il est sur le disque**. Mesuré le
2026-08-08 : 135 membres / 12,7 Mo de `node_modules` posés par un `npm install` local du dev partaient dans
chaque release, et rien ne le disait. La garde referme ça côté sortie : ce que le wheel embarque est une
**liste fermée**, et tout membre en trop échoue le build en se nommant.
"""
from __future__ import annotations

from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ModuleNotFoundError:                              # hatchling n'existe qu'au build (env isolé) ; hors
    BuildHookInterface = object                          # de là on n'importe QUE la logique pure (tests).

# Messages d'absence (constantes → testables sans instancier le hook).
_WARN_NO_SPA = (
    "[forgemaster] web/dist absente → wheel SANS UI. Lance `npm run build` (ou `forgemaster setup`) "
    "avant de packager pour embarquer l'interface."
)
_WARN_NO_CODEMAP = (
    "[forgemaster] build/vendor/codemap absent → wheel SANS code-map (Flow indisponible côté cible). "
    "Passe par `deploy/build-wheel.sh` (il stage le sibling ../code-map) pour un wheel de release."
)
_WARN_NO_TASKMAP = (
    "[forgemaster] build/vendor/taskmap absent → wheel SANS taskmap (moteur DAG ABSENT côté cible → "
    "`No module named taskmap`). Passe par `deploy/build-wheel.sh` (il stage le sibling ../task-map)."
)
_WARN_NO_RUNNER = (
    "[forgemaster] build/vendor/verify-runner absent → wheel SANS runner Tier-1.5 (`forgemaster gate verify` "
    "fail-close côté cible). Passe par `deploy/build-wheel.sh` (il stage deploy/runners)."
)
_WARN_NO_MAPS = (
    "[forgemaster] build/vendor/edition-maps absent → wheel SANS l'édition des 3 cartes hôte "
    "(`forgemaster toolchain install` REFUSE de poser : ni code-map, ni docs-map, ni frontmap). "
    "Passe par `deploy/build-wheel.sh` (il bâtit les 3 wheels depuis les siblings)."
)
_WARN_NO_BUILD = (
    "[forgemaster] src/forgemaster/_build.json absent → wheel SANS provenance de build (le signal de "
    "fraîcheur "
    "serait aveugle : `stale`/`behind_by` non calculables). Passe par `deploy/build-wheel.sh` (il tamponne "
    "le SHA de HEAD avant packaging)."
)


def plan_force_includes(root: Path) -> tuple[dict[str, str], list[str]]:
    """Décision **pure** (testable, sans hatchling) : quels artefacts sibling embarquer dans le wheel.
    Retourne `(force_include, warnings)`.
      • SPA buildée `web/dist` → `forgemaster/_web_dist` (l'UI voyage dans le wheel) ;
      • package `build/vendor/codemap` (stagé par `deploy/build-wheel.sh`) → top-level `codemap`, importable
        par le venv (`sys.executable -m codemap`, cf. src/forgemaster/codemap/index.py) — sans lui l'onglet
        Flow
        tombe (`No module named codemap`) ;
      • package `build/vendor/taskmap` (stagé par `deploy/build-wheel.sh`) → top-level `taskmap`, importé par
        `roadmap/resolver.py` (`taskmap.core`) — vendoré pour que le wheel soit auto-contenu (plus de dép git
        privée `task-map @ git+…` à cloner au `pip install`) ; sans lui le daemon tombe
        (`No module named taskmap`).
    Chaque artefact absent → un warning (dégradation gracieuse ; en dev/editable les stagings sont absents et
    l'on skippe proprement : la SPA vient de `forgemaster setup`, code-map/task-map sont déjà
    éditable-installés)."""
    root = Path(root)
    force: dict[str, str] = {}
    warnings: list[str] = []
    if (root / "web" / "dist" / "index.html").is_file():
        force["web/dist"] = "forgemaster/_web_dist"
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
    # runner Playwright du gate Tier-1.5 (`deploy/runners`, vit DANS le repo) → `forgemaster/_verify_runner` :
    # provision-ct.sh le tire du package installé (wheel = artefact unique, provision-ct.sh voyage seul).
    if (root / "build" / "vendor" / "verify-runner" / "render_check.js").is_file():
        force["build/vendor/verify-runner"] = "forgemaster/_verify_runner"
    else:
        warnings.append(_WARN_NO_RUNNER)
    # édition des 3 cartes hôte (`deploy/build-wheel.sh` les bâtit depuis les siblings) →
    # `forgemaster/_maps` : 3 wheels + `maps.json`. Elles étaient tirées de `git+<url>@main` — une réf MOBILE
    # — par `forgemaster toolchain install` ; embarquées, elles s'installent HORS-LIGNE et ÉPINGLÉES.
    if (root / "build" / "vendor" / "edition-maps" / "maps.json").is_file():
        force["build/vendor/edition-maps"] = "forgemaster/_maps"
    else:
        warnings.append(_WARN_NO_MAPS)
    # tampon de provenance de build (`deploy/build-wheel.sh` écrit le SHA de HEAD) → `forgemaster/_build.json`
    # :
    # gitignoré (artefact par-build) → EXCLU du walk de package → force-include obligatoire (comme ci-dessus).
    # Sans lui, le signal de fraîcheur serait aveugle : le faux-vert qu'on corrige.
    if (root / "src" / "forgemaster" / "_build.json").is_file():
        force["src/forgemaster/_build.json"] = "forgemaster/_build.json"
    else:
        warnings.append(_WARN_NO_BUILD)
    return force, warnings


def unexpected_wheel_members(
    names: list[str],
    *,
    src_files: set[str],
    codemap_files: set[str],
    taskmap_files: set[str],
    runner_files: set[str],
    maps_files: set[str],
) -> list[str]:
    """Garde d'**inventaire** : les membres du wheel qu'**aucune** règle n'admet.

    Une garde de *présence* (« `render_check.js` est bien là ») ne sait pas dire « **et rien d'autre** » —
    par construction elle laisse passer tout ce qui s'ajoute. Les deux questions sont complémentaires et le
    build pose les deux.

    Les quatre ensembles de fichiers **déclarés** sont calculés par `deploy/build-wheel.sh` avec
    `git ls-files` — un seul foyer de vérité, celui qui décide déjà de ce qui est stagé. La garde ne
    re-devine rien : elle confronte l'archive à ce que git suit.

    Règles (fermées) :
      • `forgemaster/_web_dist/…`      — la SPA buildée : noms **hashés** par Vite, donc un préfixe et pas
                                         une liste littérale (le seul endroit où l'inventaire est ouvert) ;
      • `forgemaster/_verify_runner/…` — **exactement** `runner_files` (`deploy/runners` suivi) ;
      • `forgemaster/_maps/…`          — **exactement** `maps_files` (les 3 wheels de cartes + `maps.json`,
                                         bâtis par le script) : une liste **fermée**, jamais un préfixe —
                                         l'édition est ce qu'on déclare, pas ce qui traîne dans le staging ;
      • `forgemaster/_build.json`      — le tampon de provenance (gitignoré, force-inclus) ;
      • `forgemaster/<reste>`          — ssi `<reste>` est suivi sous `src/forgemaster/` ;
      • `codemap/…` · `taskmap/…`      — ssi suivi chez le sibling, ou le tampon `_vendored_from.txt` ;
      • `<nom>.dist-info/…`            — métadonnées, exigées par le format wheel ;
      • toute autre racine             — **rien**.

    Retourne les membres fautifs **triés** (déterminisme du message d'erreur), pas un booléen : un build qui
    échoue doit nommer ce qu'il refuse, sinon on ne peut pas agir dessus.
    """
    vendored = {"codemap": codemap_files, "taskmap": taskmap_files}
    unexpected: list[str] = []
    for name in names:
        if name.endswith("/"):                           # entrée de dossier (rare) : jamais du contenu
            continue
        root, _, rest = name.partition("/")
        if root.endswith(".dist-info"):
            continue
        if root in vendored:
            if rest != "_vendored_from.txt" and rest not in vendored[root]:
                unexpected.append(name)
            continue
        if root == "forgemaster":
            if rest.startswith("_web_dist/") or rest == "_build.json":
                continue
            if rest.startswith("_verify_runner/"):
                if rest[len("_verify_runner/"):] not in runner_files:
                    unexpected.append(name)
                continue
            if rest.startswith("_maps/"):
                if rest[len("_maps/"):] not in maps_files:
                    unexpected.append(name)
                continue
            if rest not in src_files:
                unexpected.append(name)
            continue
        unexpected.append(name)
    return sorted(unexpected)


class ForgemasterFrontBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":                  # sdist reste source-only ; seul le wheel embarque
            return
        force, warnings = plan_force_includes(Path(self.root))
        build_data.setdefault("force_include", {}).update(force)
        for warning in warnings:
            self.app.display_warning(warning)
