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

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path

from cockpit.config import Settings
from cockpit.secrets import SecretNotFound, SecretUnsupported, build_store, cred_resolver
from cockpit.secrets.jwt import mint_hs256
from cockpit.service import set_env_keys

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


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit mcp wire` : câble NOTRE instance mcp-catalogs sur cette install. Pose une **référence
    opaque** au secret HMAC partagé (JAMAIS le secret en clair) + l'endpoint dans `cockpit.env`, de sorte que
    le prochain dispatch injecte un `.mcp.json` valide (`inject_mcp_config`). Deux voies exclusives (comme
    `onboard link`) : `--secret-file <f>` (on POSSÈDE la valeur → `store.put` → ref opaque) ou `--secret-ref
    <uuid>` (bring-your-own, VALIDÉE via `store.get`). Sans câblage, l'injection reste un no-op honnête."""
    if getattr(args, "action", None) != "wire":
        print(f"action mcp inconnue : {getattr(args, 'action', None)!r} (attendu : wire)")
        return 2
    secret_file = getattr(args, "secret_file", None)
    secret_ref = getattr(args, "secret_ref", None)
    if bool(secret_file) == bool(secret_ref):
        print("fournir exactement l'un de : --secret-file <f> (fichier) | --secret-ref <uuid> (BWS).")
        return 2
    store = build_store(settings)
    ref: str
    try:
        if secret_file:
            secret = Path(secret_file).expanduser().read_text(encoding="utf-8").strip()
            if len(secret) < 32:
                print("secret trop court (<32c) — HS256 exige un secret d'au moins 32 caractères.")
                return 1
            ref = store.put(secret, label="mcp-jwt")          # voie fichier → ref opaque générée
        else:
            ref = str(secret_ref)
            store.get(ref)                                    # voie BWS → VALIDE que la ref résout
    except SecretUnsupported as exc:
        print(f"backend {store.backend!r} : pas de secret direct — passe --secret-ref <uuid>. ({exc})")
        return 1
    except SecretNotFound as exc:
        print(f"référence introuvable dans le store {store.backend!r} : {secret_ref!r} ({exc})")
        return 1
    except OSError as exc:
        print(f"secret-file illisible — {exc}")
        return 1
    endpoint = getattr(args, "endpoint", None) or MCP_ENDPOINT
    set_env_keys(settings.home / "cockpit.env",
                 {ENV_MCP_JWT_SECRET_REF: ref, "COCKPIT_MCP_ENDPOINT": endpoint})
    print(f"✅ MCP câblé → {endpoint}  (ref opaque posée dans {settings.home / 'cockpit.env'}).")
    print("   redémarre le service pour recharger l'EnvironmentFile : "
          "`systemctl restart cockpit` (ou `--user`).")
    return 0
