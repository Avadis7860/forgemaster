"""daemon.ws_token — le **secret par-instance** qui gate les poignées WebSocket (defense-in-depth du
contrôle d'Origin, cf. `daemon.wsguard`).

Un token unique **par instance** de daemon, minté au 1er démarrage et persisté `home/ws_token` (chmod 600).
Il n'a PAS sa place dans le coffre `SecretStore` (fait pour des `credential_ref` per-projet stockés en DB et
résolus à l'usage git) : c'est un secret d'instance, unique, lu à chaque handshake — un fichier 600 sous
`home/` est le juste niveau (même dossier que la base et les logs, même frontière de confiance).

Le front same-origin l'obtient de façon transparente (endpoint same-origin, Phase B) ; une page tierce ne
peut pas le lire (la même-origin policy bloque la lecture cross-origin de la réponse) → elle ne peut pas
forger le sous-protocole `forgemaster.token.<valeur>` attendu au handshake.
"""
from __future__ import annotations

import secrets
import stat

from forgemaster.config import Settings

TOKEN_FILE = "ws_token"


def ensure_ws_token(settings: Settings) -> str:
    """Lit le token WS par-instance, ou le **minte** (une fois) s'il n'existe pas encore. Idempotent :
    relance = même token (persisté). Le fichier est créé chmod 600 sous `home/` (parents créés au besoin).

    Total/fail-safe : si un token persisté est lisible on le rend tel quel ; sinon on en génère un neuf
    (`token_urlsafe(32)` — 256 bits d'entropie, URL/sous-protocole-safe) et on l'écrit atomiquement."""
    path = settings.home / TOKEN_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    settings.home.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token, encoding="utf-8")
    tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)                  # 600 : lisible du seul propriétaire
    tmp.replace(path)                                       # pose atomique (jamais un token à moitié écrit)
    return token
