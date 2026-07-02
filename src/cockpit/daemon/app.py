"""app — construction et lancement du daemon. **Import de fastapi/uvicorn PARESSEUX** (dans le corps des
fonctions) : le module s'importe sans les deps serveur (prouvé par test_skeleton), on ne les tire qu'au
`serve`. `build_app` reçoit `settings` (+ à terme les couches) et câble les routers par **injection
explicite** — jamais un module-global mutable (correctif #1, anti god-module `import server`).

Routers découpés par domaine (correctif #3, fin du monolithe 1650-LOC) : projects / roadmap / dispatch /
gate. Statut : stub. Signatures figées (appelées par `cli.serve`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from cockpit.config import Settings

if TYPE_CHECKING:  # imports lourds réservés au typage — jamais au runtime d'import du module
    from fastapi import FastAPI


def build_app(settings: Settings) -> FastAPI:
    """Construit l'app FastAPI avec DI explicite (settings + couches). Import fastapi paresseux. À porter
    (refactor #1/#3)."""
    from fastapi import FastAPI  # noqa: F401 — import paresseux volontaire (le module s'importe sans)

    raise NotImplementedError("port: services/aggregator/server.py:build_app — #1 (DI explicite)")


def serve(settings: Settings, *, host: str, port: int) -> int:
    """Démarre uvicorn sur `build_app(settings)`. Import uvicorn paresseux. À porter (refactor #3)."""
    raise NotImplementedError("port: services/aggregator/server.py (uvicorn.run) — #3")
