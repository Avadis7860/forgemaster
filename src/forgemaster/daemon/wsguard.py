"""daemon.wsguard — la **garde partagée** des poignées WebSocket, appelée AVANT tout `accept()` par
`routes/terminal._accept_project_pty` (shell + interview) et `routes/dispatch.dispatch_ws`.

Ferme le vecteur **CSWSH** (Cross-Site WebSocket Hijacking) : une page tierce chargée dans le navigateur de
l'opérateur peut ouvrir `ws://<hôte>:<port>/ws/...` — la connexion part de la machine de l'opérateur *vers
l'intérieur*, donc routeur/pare-feu ne la filtrent pas, et la same-origin policy ne s'applique **pas** au
handshake WS (le CORS non plus). Seul un contrôle **côté serveur** l'arrête. Deux barrières composées :

1. **Origin** — barrière anti-navigateur. Le navigateur envoie toujours un `Origin` vrai, non-forgeable
   depuis une page. On l'accepte s'il est **same-origin** (son autorité == le `Host` de la requête —
   zéro-config, couvre n'importe quel hôte d'instance, LAN inclus), OU s'il est une origine de **dev Vite**,
   OU dans l'allowlist configurée (`settings.ws_allowed_origins`, cas reverse-proxy). Un `Origin` **absent**
   (client non-navigateur : sonde E2E, void-runner) est toléré — ce n'est pas le vecteur CSWSH ; le token
   reste exigé.
2. **Token par-instance** — barrière anti-client-non-navigateur + defense-in-depth. Exigé au handshake via
   le sous-protocole `forgemaster.token.<valeur>` (hors des access-logs, cf. `ws_token`). Comparé en temps
   constant. Le serveur **echo** le sous-protocole retenu à l'`accept` (obligation RFC 6455).

Les deux vérifs sont **pures** (testables sans WebSocket) ; `authorize_ws` n'est que la glue qui lit les
en-têtes/scope et ferme `1008` en cas de refus.
"""
from __future__ import annotations

import secrets
from collections.abc import Iterable
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from fastapi import WebSocket

#: Origines de développement (Vite) : cross-origin du daemon (:5173 → :8700) mais légitimes. Alignées sur
#: l'allowlist CORS (`app.py`), qui ne couvre QUE les `fetch` — cette liste-ci couvre les handshakes WS.
DEV_ORIGINS: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

#: Sigil du token dans `Sec-WebSocket-Protocol` : le client offre `forgemaster.token.<valeur>`.
TOKEN_SUBPROTOCOL_PREFIX = "forgemaster.token."


def origin_allowed(origin: str | None, host: str | None, extra: tuple[str, ...]) -> bool:
    """Vrai si le handshake est autorisé au titre de l'Origin. `origin`/`host` sont les en-têtes bruts.

    - `origin is None` → toléré (client non-navigateur ; le token reste exigé en aval). PAS le vecteur CSWSH.
    - same-origin : l'**autorité** de l'Origin (`host:port`, sans schéma) == l'en-tête `Host` → autorisé,
      sans configuration (couvre l'hôte public réel de l'instance, quel qu'il soit).
    - sinon : autorisé seulement si l'Origin exact figure dans les origines de dev ou l'allowlist configurée.
    Un Origin présent mais non-résolvable / `"null"` (iframe sandbox, file://) tombe en refus."""
    if origin is None:
        return True
    if host and urlsplit(origin).netloc == host:
        return True
    return origin in DEV_ORIGINS or origin in extra


def match_token_subprotocol(offered: Iterable[str], expected: str) -> str | None:
    """Cherche parmi les sous-protocoles offerts un `forgemaster.token.<valeur>` dont la valeur == `expected`
    (comparaison **temps-constant**). Retourne le sous-protocole **exact** à echo à l'`accept` (obligation
    RFC : le serveur doit sélectionner l'un des offerts), ou `None` si aucun ne correspond."""
    for proto in offered:
        if proto.startswith(TOKEN_SUBPROTOCOL_PREFIX):
            value = proto[len(TOKEN_SUBPROTOCOL_PREFIX):]
            if secrets.compare_digest(value, expected):
                return proto
    return None


async def authorize_ws(websocket: WebSocket) -> str | None:
    """Garde AVANT `accept()`. Lit `settings` + le token d'instance sur `app.state`, applique les deux
    barrières (Origin puis token) et, en cas de refus, ferme le WS (`1008`) et retourne `None` — l'appelant
    ne doit alors PAS `accept()`. En cas de succès, retourne le sous-protocole à passer à
    `websocket.accept(subprotocol=...)` (echo du token)."""
    settings = websocket.app.state.deps.settings
    token = websocket.app.state.ws_token
    if not origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host"),
                          settings.ws_allowed_origins):
        await websocket.close(code=1008)                   # Origin hors politique → refus AVANT accept
        return None
    matched = match_token_subprotocol(websocket.scope.get("subprotocols", ()), token)
    if matched is None:
        await websocket.close(code=1008)                   # token absent/invalide → refus AVANT accept
        return None
    return matched
