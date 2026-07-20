"""mcp.client — client MCP **runtime** du cockpit : parle au serveur `mcp-catalogs` **depuis le daemon**,
à la requête, via `fastmcp.Client`. Deux usages, un seul socle :

- `blueprint_resolver` — résout un `blueprint:` (ref → corps) pour le seam `taskmap.context` (board P3) ;
- `capital_browser` — **parcourt** en lecture le capital-token servi (navigation `list_types →
  list_collections → list_sections → read`), pour l'explorer d'introspection de la Landing.

Contrairement à `provision.mcp` (qui écrit un `.mcp.json` pour un *worker subprocess*), ici le daemon appelle
en direct. Import `fastmcp` **paresseux** (le socle cockpit ne le tire pas au chargement).

**Dégradation honnête, totale et silencieuse** (comme `secrets.cred_resolver`) : secret non câblé / trop
court, MCP injoignable, réponse vide, toute exception → **`None`**. Jamais inventé, jamais propagé.

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
McpCaller = Callable[..., "dict | list | None"]

_SUBJECT = "cockpit:board"        # sub du JWT pour la résolution blueprint (serveur valide aud/iss/signature)
_SUBJECT_CAPITAL = "cockpit:capital"  # sub du JWT pour le parcours du capital-token (surface Landing)
_TTL_SECONDS = 300                # token éphémère : un appel daemon est court, minté frais à chaque requête
_DEFAULT_TIMEOUT = 5.0            # borne l'appel : un MCP pendu ne stalle pas la requête → None honnête


def _mint_or_none(*, secret_ref: str | None, resolve_secret: Callable[[str], str],
                  subject: str) -> str | None:
    """Mint un JWT HS256 pour le MCP, ou **`None`** (dégradation honnête, avant tout appel réseau) : ref
    absent (MCP non câblé) → None ; secret <32c (absent/illisible/mal configuré) → None ; mint KO → None.
    PUR hors la résolution du secret. Partagé par les deux clients (blueprint + capital)."""
    ref = secret_ref if secret_ref is not None else os.environ.get(ENV_MCP_JWT_SECRET_REF, "")
    if not ref:
        return None                                        # MCP non câblé → délégation honnête (pas de MCP)
    secret = resolve_secret(ref)
    if len(secret) < 32:                                   # absent / illisible / mal configuré → no-op
        return None
    try:
        return mint_hs256(subject, secret, audience=MCP_AUDIENCE, issuer=MCP_ISSUER, ttl_seconds=_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — mint impossible → dégradation honnête, jamais propagée
        return None


def _call_tool(endpoint: str, token: str, tool: str, arguments: dict, *,
               timeout: float) -> dict | list | None:
    """Coquille réseau **générique** : appelle n'importe quel outil MCP (`fastmcp.Client`, Streamable HTTP +
    Bearer) et retourne son corps structuré (`.data`) ou `None`. Import fastmcp **paresseux**. Le daemon
    appelle depuis un thread sync (routes `def`) → `asyncio.run` est sûr (aucun event-loop courant)."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    async def _call() -> dict | list | None:
        transport = StreamableHttpTransport(endpoint, headers={"Authorization": f"Bearer {token}"})
        async with Client(transport, timeout=timeout) as client:
            result = await client.call_tool(tool, arguments)
        return result.data

    return asyncio.run(_call())


def _read_blueprint(endpoint: str, token: str, bp_id: str, *, timeout: float) -> dict | None:
    """Coquille réseau du resolver blueprint : `read(type=blueprint, ref=<id>)`. Thin wrapper sur `_call_tool`
    (contrat historique du seam `caller` de `blueprint_resolver`)."""
    data = _call_tool(endpoint, token, "read", {"type": "blueprint", "ref": bp_id}, timeout=timeout)
    return data if isinstance(data, dict) else None


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
        token = _mint_or_none(secret_ref=secret_ref, resolve_secret=resolve_secret, subject=_SUBJECT)
        if token is None:
            return None
        try:
            data = caller(ep, token, bp_id, timeout=timeout)
        except Exception:  # noqa: BLE001 — panne MCP/réseau → liaison morte honnête, jamais propagée
            return None
        return data if isinstance(data, dict) and data else None

    return resolve


class CapitalBrowser:
    """Client de **parcours read-only** du capital-token servi par `mcp-catalogs` (navigation
    `list_types → list_collections → list_sections → read`). Chaque méthode mint un JWT frais et appelle
    l'outil MCP éponyme ; **dégradation honnête totale** — MCP non câblé / injoignable / réponse vide →
    `None`, jamais d'exception propagée (contrat du module). Instancié via `capital_browser` (seams
    injectés)."""

    def __init__(self, *, endpoint: str, resolve_secret: Callable[[str], str], secret_ref: str | None,
                 caller: McpCaller, timeout: float) -> None:
        self._endpoint = endpoint
        self._resolve_secret = resolve_secret
        self._secret_ref = secret_ref
        self._caller = caller
        self._timeout = timeout

    def _invoke(self, tool: str, arguments: dict) -> dict | list | None:
        token = _mint_or_none(secret_ref=self._secret_ref, resolve_secret=self._resolve_secret,
                              subject=_SUBJECT_CAPITAL)
        if token is None:
            return None                                    # MCP non câblé → None honnête (pas d'appel réseau)
        try:
            return self._caller(self._endpoint, token, tool, arguments, timeout=self._timeout)
        except Exception:  # noqa: BLE001 — panne MCP/réseau → None honnête, jamais propagée
            return None

    def list_types(self) -> dict | list | None:
        """Les **types** servis (`tech`, `blueprint`, `templates`) + leur stratégie. `None` si MCP indispo."""
        return self._invoke("list_types", {})

    def list_collections(self, type: str) -> dict | list | None:
        """Les **collections** (silos) d'un type. `None` si MCP indispo."""
        return self._invoke("list_collections", {"type": type})

    def list_sections(self, type: str, scope: str | None = None) -> dict | list | None:
        """La **TOC** (sections) d'un type : `scope` requis pour un silo (`tech`), omis (None) pour un corpus
        plat (`blueprint`). `None` si MCP indispo."""
        return self._invoke("list_sections", {"type": type, "scope": scope})

    def read(self, type: str, ref: str) -> dict | list | None:
        """Le **corps** d'une ref (divulgation progressive). `None` si MCP indispo."""
        return self._invoke("read", {"type": type, "ref": ref})


def capital_browser(settings: Settings, *, secret_ref: str | None = None,
                    endpoint: str | None = None,
                    resolver: Callable[[str], str] | None = None,
                    caller: McpCaller = _call_tool,
                    timeout: float = _DEFAULT_TIMEOUT) -> CapitalBrowser:
    """Rend un `CapitalBrowser` adossé au MCP `mcp-catalogs`, pour l'explorer d'introspection de la Landing.
    Mêmes **seams** que `blueprint_resolver` (défauts : env `COCKPIT_MCP_JWT_SECRET_REF`, `MCP_ENDPOINT`,
    `cred_resolver`, la coquille réseau générique `_call_tool`) — tout injectable pour un test sans réseau."""
    resolve_secret = resolver if resolver is not None else cred_resolver(settings)
    ep = endpoint if endpoint is not None else MCP_ENDPOINT
    return CapitalBrowser(endpoint=ep, resolve_secret=resolve_secret, secret_ref=secret_ref,
                          caller=caller, timeout=timeout)
