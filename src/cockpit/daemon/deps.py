"""deps — le conteneur d'injection **explicite** du daemon. Construit UNE fois par `build_app` et posé sur
`app.state.deps` ; chaque router le lit via la dépendance `get_deps` (paramètre `Depends(get_deps)`).

C'est le correctif #1 (anti god-module) rendu concret : le legacy `server.py` était un module-global que
tous les routers faisaient `import server` pour lire les seams (`server.terminal`, `server.SSH_KEY_PATH`…).
Ici rien de global mutable — les couches (registry/model/resolver/worker/gate) sont importées à l'appel et
reçoivent `settings` + une connexion ouverte par `Deps`. Ce module ne tire que `starlette` (transitif de
fastapi, nécessaire pour typer `get_deps`), jamais les couches serveur lourdes ; il est chargé par
`build_app` uniquement.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# `Request` importé au RUNTIME (pas sous TYPE_CHECKING) : avec `from __future__ import annotations`, FastAPI
# résout l'annotation à l'exécution pour injecter la Request — absente du namespace, elle serait prise pour
# un query param (422). starlette est un transitif de fastapi, déjà présent dans le chemin `serve`.
from starlette.requests import Request

from cockpit.config import Settings
from cockpit.db import store


@dataclass(frozen=True)
class Deps:
    """Dépendances du daemon (immuable). Tient les racines résolues ; ouvre des connexions DB à la demande
    (une par requête, refermée par le router — WAL autorise la concurrence CLI↔daemon)."""

    settings: Settings

    def open_db(self) -> sqlite3.Connection:
        """Connexion migrée à la base du cockpit (à refermer par l'appelant)."""
        return store.open_db(self.settings)


def get_deps(request: Request) -> Deps:
    """Dépendance FastAPI : rend le conteneur `Deps` posé sur `app.state` par `build_app`. Aucun global —
    l'état vit sur l'app, injecté explicitement."""
    return request.app.state.deps
