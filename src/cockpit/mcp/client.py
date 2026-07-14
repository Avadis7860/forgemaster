"""mcp.client — client MCP **runtime** du cockpit : résout un `blueprint:` (ref) en son corps, en direct.

Contrairement à `provision.mcp` (qui écrit un `.mcp.json` pour un *worker subprocess*), ce module appelle le
serveur `mcp-catalogs` **depuis le daemon**, à la requête, via `fastmcp.Client` — pour alimenter le seam
`resolve_blueprint` de `taskmap.context` (`BlueprintResolver = Callable[[str], dict|None]`). Le board (P3)
s'en sert pour faire ressortir `resolved:true` sur un `features.blueprint`.

**Dégradation honnête, totale et silencieuse** (comme `secrets.cred_resolver`) : secret non câblé / trop
court, MCP injoignable, réponse vide, toute exception → **`None`**. Jamais inventé, jamais propagé — c'est
exactement le contrat qu'attend `taskmap.context._blueprint_verdict` (`None`/`{}` → liaison morte signalée).

Réutilise le **contrat serveur** déjà prouvé (`provision.mcp` : endpoint/aud/iss/ref) + le minteur HS256
stdlib (`secrets.jwt`) + le résolveur de secret (`secrets.cred_resolver`) — zéro redéclaration.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from cockpit.config import Settings
from cockpit.provision.mcp import (
    ENV_MCP_JWT_SECRET_REF,
    MCP_AUDIENCE,
    MCP_ENDPOINT,
    MCP_ISSUER,
)
from cockpit.secrets import cred_resolver
from cockpit.secrets.jwt import mint_hs256

BlueprintResolver = Callable[[str], "dict | None"]

_SUBJECT = "cockpit:board"      # sub du JWT (le serveur valide aud/iss/signature ; sub = traçabilité)
_TTL_SECONDS = 300              # token éphémère : une résolution board est courte, minté frais à chaque appel
_DEFAULT_TIMEOUT = 5.0          # borne l'appel : un MCP pendu ne stalle pas la requête board → None honnête


def _read_blueprint(endpoint: str, token: str, bp_id: str, *, timeout: float) -> dict | None:
    """Coquille réseau réelle : `read(type=blueprint, ref=<id>)` via `fastmcp.Client` (Streamable HTTP +
    Bearer). Retourne le corps structuré (`.data`) ou `None`. Import fastmcp **paresseux** (le socle cockpit
    ne tire pas fastmcp au chargement). Le daemon appelle depuis un thread sync (routes `def`) → `asyncio.run`
    est sûr (aucun event-loop courant)."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    async def _call() -> dict | None:
        transport = StreamableHttpTransport(endpoint, headers={"Authorization": f"Bearer {token}"})
        async with Client(transport, timeout=timeout) as client:
            result = await client.call_tool("read", {"type": "blueprint", "ref": bp_id})
        return result.data

    return asyncio.run(_call())


def blueprint_resolver(settings: Settings, *, secret_ref: str | None = None,
                       endpoint: str | None = None,
                       resolver: Callable[[str], str] | None = None,
                       caller: Callable[..., dict | None] = _read_blueprint,
                       timeout: float = _DEFAULT_TIMEOUT) -> BlueprintResolver:
    """Rend un `BlueprintResolver` (`id -> dict|None`) adossé au MCP `mcp-catalogs`, injectable au seam
    `taskmap.context.build_context`/`doctor`. `secret_ref`/`endpoint`/`resolver`/`caller` sont des **seams**
    (défauts : env `COCKPIT_MCP_JWT_SECRET_REF`, `MCP_ENDPOINT`, `cred_resolver`, la coquille réseau réelle) —
    tout est injectable pour un test factice sans réseau. Dégradation honnête : voir le module."""
    resolve_secret = resolver if resolver is not None else cred_resolver(settings)
    ep = endpoint if endpoint is not None else MCP_ENDPOINT

    def resolve(bp_id: str) -> dict | None:
        ref = secret_ref if secret_ref is not None else os.environ.get(ENV_MCP_JWT_SECRET_REF, "")
        if not ref:
            return None                                    # MCP non câblé → délégation honnête (pas de MCP)
        secret = resolve_secret(ref)
        if len(secret) < 32:                               # absent / illisible / mal configuré → no-op
            return None
        try:
            token = mint_hs256(_SUBJECT, secret, audience=MCP_AUDIENCE, issuer=MCP_ISSUER,
                               ttl_seconds=_TTL_SECONDS)
            data = caller(ep, token, bp_id, timeout=timeout)
        except Exception:  # noqa: BLE001 — panne MCP/réseau/mint → liaison morte honnête, jamais propagée
            return None
        return data if isinstance(data, dict) and data else None

    return resolve
