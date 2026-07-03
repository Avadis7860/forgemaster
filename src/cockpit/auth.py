"""auth — détection déterministe de l'authentification Claude de l'HÔTE, **sans jamais lire le secret**.

Le cockpit dispatche des workers `claude -p` **locaux** : ils s'authentifient via le CLI `claude` de la
machine — `$HOME/.claude/.credentials.json` (OAuth, voie normale d'un `claude login`) ou, à défaut, une
clé d'environnement (`ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`). Ce module répond à une seule
question : « **cette machine est-elle authentifiée ?** » — pour (1) l'onboarding (surface l'état, jamais
silencieux) et (2) le **gate de dispatch** (refuser un spawn tant que l'utilisateur n'a pas authentifié
**son** compte). On ne lit JAMAIS la valeur du token : la **présence** est le signal, rien de plus.

Modèle : auth **par machine** (le CLI `claude` lit `$HOME`), pas par projet. Le cockpit n'embarque, ne
partage et n'injecte aucun credential — il utilise l'auth **officielle** du CLI. Un install public exige
donc que chaque hôte fasse `claude login` avec **son** compte (cf. `AUTH_HINT`)."""
from __future__ import annotations

import os
from pathlib import Path

_CREDENTIALS_REL = ".claude/.credentials.json"

# Message actionnable UNIQUE (CLI + API + onboarding) — pointe vers la voie officielle, dans LE terminal.
AUTH_HINT = (
    "compte Claude non authentifié sur cette machine. Lance `claude login` dans le terminal pour "
    "authentifier TON compte. Le cockpit utilise l'auth officielle du CLI `claude` — jamais un token "
    "partagé ni embarqué."
)


def claude_auth_status(home: Path | None = None, env: dict[str, str] | None = None) -> dict:
    """État d'auth Claude de l'hôte, **sans lire le secret**. Renvoie `{authenticated, source}` où `source`
    ∈ {`credentials-file`, `env-api-key`, `env-oauth`, `None`}. Déterministe : présence d'un fichier /
    d'une clé d'env, jamais la valeur. `home`/`env` injectables (tests) ; défaut = `$HOME` réel + `os.environ`
    (le même contexte que le `claude` qu'un worker spawnera)."""
    home_dir = Path.home() if home is None else home
    environ: dict[str, str] = dict(os.environ) if env is None else env
    if (home_dir / _CREDENTIALS_REL).is_file():
        return {"authenticated": True, "source": "credentials-file"}
    if environ.get("ANTHROPIC_API_KEY"):
        return {"authenticated": True, "source": "env-api-key"}
    if environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return {"authenticated": True, "source": "env-oauth"}
    return {"authenticated": False, "source": None}
