"""provision.mcp — câblage du MCP de corpus dans un worktree de worker (injection POST-création).

Un worker dispatché sur un projet typé (browser-game…) doit pouvoir interroger le MCP `mcp-catalogs`
(`query(type=tech, scope=<silo>)`) — c'est la moitié « il connaît ses outils » du crash-test. Ce module
rend et écrit le `.mcp.json` que `claude -p` charge (via `--mcp-config`), avec un **JWT minté à la demande**,
**jamais baké** dans le bundle/wheel/SoT (décision d'épic : câblage hors-git, injecté au dispatch).

Sécurité (load-bearing) : le `.mcp.json` porte un Bearer → il est **gitignoré** dans le bundle de base, si
bien que le `git add -A` du commit de la forge ne peut jamais l'embarquer. Le secret partagé
(`MCP_JWT_SECRET`) est résolu par le coffre du cockpit à l'usage ; absent/illisible → **no-op honnête** (le
worker tourne sans MCP, aucun crash — dégradation prévue pour un install public sans le corpus privé).

Nommage : le label serveur et l'`aud`/`iss` du JWT reproduisent **verbatim le contrat validé par le serveur
mcp-catalogs** (CT 9118) — hérité de l'ex-CT 9113. Le renommage `vault-catalogs → mcp-catalogs` est un
retrait de verbatim historique **coordonné** (serveur-d'abord), suivi hors d'ici (backlog vault
`mcp-catalogs-naming-coherence`) — surtout pas une demi-migration côté client seul.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from cockpit.config import Settings
from cockpit.secrets import cred_resolver
from cockpit.secrets.jwt import mint_hs256

# Endpoint du serveur mcp-catalogs (HTTP + JWT, CT 9118). Non secret → override simple par env.
MCP_ENDPOINT = os.environ.get("COCKPIT_MCP_ENDPOINT", "http://192.168.0.153:8080/mcp")
# Contrat prouvé accepté par le serveur (verbatim ex-CT 9113 ; cf. backlog mcp-catalogs-naming-coherence).
MCP_SERVER_LABEL = "vault-catalogs"
MCP_AUDIENCE = "vault-catalogs"
MCP_ISSUER = "vault-mcp"
# Référence (dans le coffre du cockpit) du secret HMAC partagé qui signe les JWT du MCP. Absent → pas de MCP.
ENV_MCP_JWT_SECRET_REF = "COCKPIT_MCP_JWT_SECRET_REF"
_TTL_SECONDS = 86400  # 1 jour : couvre un dispatch long (≤1800s) avec marge, sans token longue-vie.

_MCP_FILENAME = ".mcp.json"


def render_mcp_config(token: str, *, endpoint: str = MCP_ENDPOINT) -> dict:
    """Forme du `.mcp.json` que `claude -p` charge : un serveur MCP http + Bearer. PUR (aucune I/O)."""
    return {
        "mcpServers": {
            MCP_SERVER_LABEL: {
                "type": "http",
                "url": endpoint,
                "headers": {"Authorization": f"Bearer {token}"},
            },
        }
    }


def inject_mcp_config(worktree: Path, settings: Settings, *, slug: str,
                      resolver: Callable[[str], str] | None = None,
                      secret_ref: str | None = None) -> Path | None:
    """Écrit `<worktree>/.mcp.json` (chmod 600) pour que le worker de `slug` interroge le MCP de corpus.

    Résout le secret HMAC partagé via le coffre (`resolver`, défaut = `cred_resolver(settings)` — **total** :
    `''` si absent/illisible). Mint un JWT scopé `sub=cockpit:<slug>` (aud/iss du contrat serveur). **No-op
    honnête** (retourne `None`, aucun fichier) si le ref n'est pas configuré ou si le secret est absent/trop
    court — le dispatch ne doit jamais crasher sur le câblage MCP. Retourne le chemin écrit sinon."""
    ref = secret_ref if secret_ref is not None else os.environ.get(ENV_MCP_JWT_SECRET_REF, "")
    if not ref:
        return None
    resolve = resolver or cred_resolver(settings)
    secret = resolve(ref)
    if len(secret) < 32:                                   # secret absent / illisible / mal configuré
        return None
    token = mint_hs256(f"cockpit:{slug}", secret, audience=MCP_AUDIENCE, issuer=MCP_ISSUER,
                       ttl_seconds=_TTL_SECONDS)
    path = worktree / _MCP_FILENAME
    path.write_text(json.dumps(render_mcp_config(token), indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)                                      # porte le Bearer — lecture propriétaire seule
    return path
