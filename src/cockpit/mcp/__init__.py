"""mcp — client MCP runtime du cockpit : résolution `blueprint:` en direct + parcours du capital-token servi.

Reste **stdlib-léger au chargement** : `fastmcp` est importé paresseusement dans `client._call_tool` (seule la
coquille réseau réelle le tire). Cf. `client` pour le contrat de dégradation honnête totale.
"""
from __future__ import annotations

from cockpit.mcp.client import (
    BlueprintResolver,
    CapitalBrowser,
    blueprint_resolver,
    capital_browser,
)

__all__ = ["BlueprintResolver", "CapitalBrowser", "blueprint_resolver", "capital_browser"]
