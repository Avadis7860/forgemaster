"""mcp — client MCP runtime du cockpit (résolution `blueprint:` en direct via `mcp-catalogs`).

Reste **stdlib-léger au chargement** : `fastmcp` est importé paresseusement dans `client._read_blueprint`
(seule la coquille réseau réelle le tire). Cf. `client` pour le contrat de dégradation honnête.
"""
from __future__ import annotations

from cockpit.mcp.client import BlueprintResolver, blueprint_resolver

__all__ = ["BlueprintResolver", "blueprint_resolver"]
