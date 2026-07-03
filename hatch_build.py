"""hatch_build.py — hook de packaging : embarque la SPA buildée dans le wheel sous `cockpit/_web_dist`.

Distribution turnkey : **l'utilisateur final n'installe que Python** ; l'UI voyage dans le wheel. Le hook est
**volontairement minimal** — il *force-include* `web/dist` si elle existe, il ne lance PAS npm lui-même :
  • builder le front est le rôle de `cockpit setup` (from-clone) ou de la CI (`npm run build`) **avant** de
    packager un wheel de release ;
  • lancer npm dans le hook le ferait tourner aussi sur `pip install -e` (l'éditable passe par la cible
    `wheel`) → footgun sur chaque install dev. On l'évite.
Si `web/dist` est absente, on émet un warning clair et on packe sans UI (`cockpit setup` la posera au runtime).
Ne re-committe jamais la dist dans git (respecte `docs/specs/web-cockpit-spa.md`).
"""
from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CockpitFrontBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":                  # sdist reste source-only ; seul le wheel embarque l'UI
            return
        dist = Path(self.root) / "web" / "dist"
        if (dist / "index.html").is_file():
            build_data.setdefault("force_include", {})["web/dist"] = "cockpit/_web_dist"
        else:
            self.app.display_warning(
                "[cockpit] web/dist absente → wheel SANS UI. Lance `npm run build` (ou `cockpit setup`) "
                "avant de packager pour embarquer l'interface."
            )
