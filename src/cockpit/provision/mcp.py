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
from cockpit.secrets.jwt import b64url_decode, mint_hs256
from cockpit.service import set_env_keys

ENV_MCP_ENDPOINT = "COCKPIT_MCP_ENDPOINT"


def current_endpoint() -> str | None:
    """L'endpoint MCP EFFECTIF, résolu à l'APPEL (jamais gelé) : `os.environ['COCKPIT_MCP_ENDPOINT']` — que
    `wire(live_env=True)` met à jour LIVE. Résolu à l'appel et non gelé à l'import parce que, sans ça, après
    un câblage vers un autre endpoint le daemon interrogeait encore l'ancien (503 « MCP non câblé ») et le
    worker pointait l'ancien (footgun constaté 2026-07-30, démo laptop MCP servi en local).

    **`None` = aucun endpoint configuré, et c'est un état NORMAL** — une install sans corpus privé n'a pas
    d'instance mcp-catalogs à interroger. Ce module portait jusqu'au 2026-08-03 un défaut en dur vers NOTRE
    CT (`192.168.0.153`) : un défaut de produit qui ne survivait que parce que personne d'autre que nous ne
    l'exécutait. Un cockpit n'a **pas** d'instance MCP par défaut ; l'absence se dit (`None`), elle ne se
    devine pas. Les appelants dégradent honnêtement : pas d'endpoint ⇒ pas de MCP."""
    return os.environ.get(ENV_MCP_ENDPOINT) or None
# Contrat prouvé accepté par le serveur (verbatim ex-CT 9113 ; cf. backlog mcp-catalogs-naming-coherence).
MCP_SERVER_LABEL = "vault-catalogs"
MCP_AUDIENCE = "vault-catalogs"
MCP_ISSUER = "vault-mcp"
# Référence (dans le coffre du cockpit) du secret HMAC partagé qui signe les JWT du MCP. Absent → pas de MCP.
ENV_MCP_JWT_SECRET_REF = "COCKPIT_MCP_JWT_SECRET_REF"
_TTL_SECONDS = 86400  # 1 jour : couvre un dispatch long (≤1800s) avec marge, sans token longue-vie.
# Décision de cycle de vie (P4) : le token est RE-MINTÉ à chaque dispatch (`inject_mcp_config` écrase le
# `.mcp.json`) → just-in-time, jamais expiré au lancement d'un run frais. Le doctor ne signale donc un token
# expiré/expirant que sur un worktree NON re-dispatché (le faux-négatif void-runner) — fenêtre = un run.
_RUN_WINDOW_S = 1800.0  # fenêtre d'un dispatch (cf. worker.DISPATCH_TIMEOUT) : token qui expire avant = mort.

_MCP_FILENAME = ".mcp.json"


def render_mcp_config(token: str, *, endpoint: str | None = None) -> dict:
    """Forme du `.mcp.json` que `claude -p` charge : un serveur MCP http + Bearer. `endpoint=None` (défaut) →
    résolu LIVE via `current_endpoint()` (jamais gelé à l'import). PUR (hors lecture d'`os.environ`).

    Lève `ValueError` si aucun endpoint n'est résolu : il n'y a pas de cible par défaut, et écrire un
    `.mcp.json` sans URL servirait au worker une config muette. Les appelants qui doivent dégrader (plutôt
    que crasher) testent `current_endpoint()` AVANT — cf. `inject_mcp_config`, no-op honnête."""
    ep = endpoint or current_endpoint()
    if not ep:
        raise ValueError(
            f"aucun endpoint MCP configuré ({ENV_MCP_ENDPOINT}) — il n'y a pas de cible par défaut ; "
            "câble une instance (`cockpit mcp wire --endpoint <url>`) ou passe `endpoint=`.")
    return {
        "mcpServers": {
            MCP_SERVER_LABEL: {
                "type": "http",
                "url": ep,
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
    honnête** (retourne `None`, aucun fichier) si **aucun endpoint n'est configuré**, si le ref n'est pas
    configuré, ou si le secret est absent/trop court — le dispatch ne doit jamais crasher sur le câblage MCP.
    Retourne le chemin écrit sinon."""
    endpoint = current_endpoint()
    if not endpoint:                                       # aucune instance MCP câblée → pas de MCP, dit
        return None                                        # par l'absence de fichier (le worker tourne sans)
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
    path.write_text(json.dumps(render_mcp_config(token, endpoint=endpoint), indent=2) + "\n",
                    encoding="utf-8")
    path.chmod(0o600)                                      # porte le Bearer — lecture propriétaire seule
    return path


# -- cycle de vie du token (P4 : détection d'expiration, déterministe, zéro réseau) -----------------

def token_exp(token: str) -> int | None:
    """L'`exp` (epoch) d'un JWT **sans le vérifier** — pour DÉTECTER une expiration (le doctor signale, il
    n'authentifie pas). None si le token est malformé ou sans `exp`. PUR."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        claims = json.loads(b64url_decode(parts[1]))
    except Exception:                                      # noqa: BLE001 — token malformé → pas d'exp lisible
        return None
    exp = claims.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def worktree_token(worktree: Path) -> str | None:
    """Le Bearer du `.mcp.json` d'un worktree (le token réellement servi au worker), ou None (fichier absent /
    illisible / forme inattendue). PUR."""
    path = Path(worktree) / _MCP_FILENAME
    if not path.is_file():
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        auth = cfg["mcpServers"][MCP_SERVER_LABEL]["headers"]["Authorization"]
    except Exception:                                      # noqa: BLE001 — .mcp.json absent/malformé
        return None
    return auth.removeprefix("Bearer ").strip() or None


def check_lifecycle(settings: Settings, *, now: int, window_s: float = _RUN_WINDOW_S,
                    resolver: Callable[[str], str] | None = None,
                    secret_ref: str | None = None) -> dict:
    """Diagnostic **déterministe** (zéro réseau) du cycle de vie du token MCP, pour `cockpit doctor`. Retour :
    `{configured, healthy, reason, exp, stale}`.

    - **non câblé** (pas de `COCKPIT_MCP_JWT_SECRET_REF`) → `configured=False, healthy=True` (install public
      sans corpus privé — dégradation prévue, pas une erreur) ;
    - **ref posée mais secret illisible/court** → `configured=True, healthy=False` (câblage cassé → wire) ;
    - **ref posée mais aucun endpoint** → `configured=True, healthy=False` : câblage à moitié fait. Sans
      défaut en dur (depuis le 2026-08-03), `inject_mcp_config` no-ope **silencieusement** dans cet état —
      le doctor est l'endroit qui doit le dire, sinon le worker part sans MCP sans qu'un mot soit prononcé ;
    - **sinon** : mint un token témoin (ce qu'un dispatch minterait → prouve config + TTL) et scanne
      les `.mcp.json` des worktrees ; un token **expiré ou expirant dans la fenêtre d'un run** = `stale` (le
      faux-négatif void-runner : worktree non re-dispatché). `healthy` ⇔ mint OK et aucun stale."""
    ref = secret_ref if secret_ref is not None else os.environ.get(ENV_MCP_JWT_SECRET_REF, "")
    if not ref:
        return {"configured": False, "healthy": True, "exp": None, "stale": [],
                "reason": "MCP non câblé (install sans corpus privé)"}
    if not current_endpoint():
        return {"configured": True, "healthy": False, "exp": None, "stale": [],
                "reason": f"aucun endpoint MCP ({ENV_MCP_ENDPOINT}) — ref de secret posée mais pas de "
                          "cible : le dispatch n'injectera aucun `.mcp.json` (`cockpit mcp wire --endpoint`)"}
    resolve = resolver or cred_resolver(settings)
    secret = resolve(ref)
    if len(secret) < 32:
        return {"configured": True, "healthy": False, "exp": None, "stale": [],
                "reason": "secret MCP illisible/mal configuré (ref posée mais secret absent/trop court)"}
    probe = mint_hs256("cockpit:doctor", secret, audience=MCP_AUDIENCE, issuer=MCP_ISSUER,
                       ttl_seconds=_TTL_SECONDS)
    stale: list[dict] = []
    for wt_mcp in sorted(settings.projects_root.glob(f"*/worktrees/*/{_MCP_FILENAME}")):
        tok = worktree_token(wt_mcp.parent)
        exp = token_exp(tok) if tok else None
        if exp is not None and exp <= now + window_s:      # expiré, ou expire avant la fin d'un run
            stale.append({"worktree": str(wt_mcp.parent), "exp": exp})
    healthy = not stale
    reason = "" if healthy else f"{len(stale)} worktree(s) portent un token MCP expiré/expirant"
    return {"configured": True, "healthy": healthy, "exp": token_exp(probe), "stale": stale, "reason": reason}


def wire_state() -> dict:
    """État de câblage MCP tel que VU par le daemon courant (env vivant, sans restart) : `wired` ssi une ref
    de secret est présente, `endpoint` effectif — **`None` si aucun n'est configuré** (il n'y a pas de cible
    par défaut). Alimente le wizard pour montrer/sauter l'étape — signal léger, orthogonal au diagnostic
    riche de `check_lifecycle` (doctor)."""
    return {
        "wired": bool(os.environ.get(ENV_MCP_JWT_SECRET_REF)),
        "endpoint": current_endpoint(),
    }


class MCPWireError(ValueError):
    """Câblage MCP impossible : mauvais usage, secret trop court, backend incompatible, ou ref introuvable.
    Message humain (`str(exc)`) réutilisable tel quel par la CLI et la route onboarding."""


def wire(settings: Settings, *, secret_ref: str | None = None, secret: str | None = None,
         secret_file: str | None = None, endpoint: str | None = None, live_env: bool = False) -> str:
    """Câble NOTRE instance mcp-catalogs sur cette install : valide/pose le secret HMAC partagé et écrit une
    **référence opaque** (JAMAIS le secret en clair) + l'endpoint dans `cockpit.env`, de sorte que le prochain
    dispatch injecte un `.mcp.json` valide (`inject_mcp_config`). Retourne la ref posée. Cœur partagé par la
    CLI (`cockpit mcp wire`) et la route onboarding (wizard).

    **Exactement une** voie parmi : `secret` (valeur brute POSSÉDÉE — le wizard POST le secret → `store.put`
    → ref opaque), `secret_file` (même voie, depuis un fichier — la CLI), ou `secret_ref` (bring-your-own
    UUID, VALIDÉE via `store.get`). `live_env=True` reflète aussi la ref dans `os.environ` du process courant
    → le daemon la voit **sans restart** (wizard). Lève `MCPWireError` sur mauvais usage / backend
    incompatible / secret trop court / ref introuvable / **endpoint absent**.

    L'endpoint est **obligatoire** (`endpoint=` ou déjà dans l'env) depuis le 2026-08-03 : il n'y a plus de
    cible en dur, et câbler un secret sans dire vers QUOI produirait un câblage à moitié fait, silencieux au
    dispatch. Validé AVANT tout effet de bord (rien n'est écrit dans le coffre si l'appel est incomplet)."""
    if sum(x is not None for x in (secret, secret_file, secret_ref)) != 1:
        raise MCPWireError(
            "fournir exactement l'un de : secret (valeur) | secret_file (fichier) | secret_ref (BWS/UUID).")
    ep = endpoint or current_endpoint()
    if not ep:
        raise MCPWireError(
            f"aucun endpoint MCP — passe `--endpoint <url>` (ou pose {ENV_MCP_ENDPOINT} dans l'env) : "
            "un cockpit n'a pas d'instance mcp-catalogs par défaut.")
    store = build_store(settings)
    if secret_file is not None:
        try:
            secret = Path(secret_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MCPWireError(f"secret-file illisible — {exc}") from exc
    if secret is not None:                                     # voie valeur/fichier → on POSSÈDE la valeur
        if len(secret) < 32:
            raise MCPWireError("secret trop court (<32c) — HS256 exige un secret d'au moins 32 caractères.")
        try:
            ref = store.put(secret, label="mcp-jwt")          # → ref opaque générée
        except SecretUnsupported as exc:
            raise MCPWireError(
                f"backend {store.backend!r} : pas de secret direct (bring-your-own) — passe une ref (UUID)."
            ) from exc
    else:
        ref = str(secret_ref)
        try:
            store.get(ref)                                    # voie BWS → VALIDE que la ref résout
        except SecretNotFound as exc:
            raise MCPWireError(f"référence introuvable dans le store {store.backend!r} : {ref!r}") from exc
    set_env_keys(settings.home / "cockpit.env", {ENV_MCP_JWT_SECRET_REF: ref, ENV_MCP_ENDPOINT: ep})
    if live_env:                                              # le daemon voit la ref sans recharger l'unit
        os.environ[ENV_MCP_JWT_SECRET_REF] = ref
        os.environ[ENV_MCP_ENDPOINT] = ep
    return ref


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit mcp wire` : câble le MCP de corpus (délègue à `wire()`). Deux voies exclusives
    `--secret-file <f>` (on POSSÈDE la valeur) | `--secret-ref <uuid>` (bring-your-own, BWS). Sans câblage,
    l'injection reste un no-op honnête."""
    if getattr(args, "action", None) != "wire":
        print(f"action mcp inconnue : {getattr(args, 'action', None)!r} (attendu : wire)")
        return 2
    try:
        wire(settings, secret_ref=getattr(args, "secret_ref", None),
             secret_file=getattr(args, "secret_file", None), endpoint=getattr(args, "endpoint", None))
    except MCPWireError as exc:
        print(str(exc))
        return 2 if "exactement l'un" in str(exc) else 1
    endpoint = getattr(args, "endpoint", None) or current_endpoint()
    print(f"✅ MCP câblé → {endpoint}  (ref opaque posée dans {settings.home / 'cockpit.env'}).")
    print("   redémarre le service pour recharger l'EnvironmentFile : "
          "`systemctl restart cockpit` (ou `--user`).")
    return 0
