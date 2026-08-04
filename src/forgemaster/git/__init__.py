"""git — backend git du forgemaster : interface `GitBackend` (signatures figées) + adapters `InternalGit`
(bare repo local, V1) et `GitHubGit` (P6). Toute op git passe par cette frontière → le reste du forgemaster
ne connaît que l'interface (correctif anti god-module : plus de `server.git_ops` tapé partout)."""
from __future__ import annotations
