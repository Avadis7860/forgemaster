"""config — le SOCLE : résolveur générique des racines du forgemaster. Aucune notion de vault, proxmox, CT
ou ssh (correctif : le legacy `server.py` codait `SSH_KEY_PATH`, `resolve_ctid`, `/home/dev` en dur).

Deux racines, résolues indépendamment par priorité **override explicite > env > défaut** :
- `home` — l'état du forgemaster (base SQLite, logs de jobs). `FORGEMASTER_HOME`, défaut `~/.forgemaster`.
- `projects_root` — où vivent les repos des projets gérés (SoT bare + worktrees). `FORGEMASTER_PROJECTS_ROOT`,
  défaut `~/projects`.

`Settings` est **gelé** (immuable) et se dérive une fois au démarrage (CLI/daemon), puis se passe
explicitement aux couches — jamais un module-global mutable (correctif anti god-module `import server`).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "FORGEMASTER_HOME"
ENV_PROJECTS_ROOT = "FORGEMASTER_PROJECTS_ROOT"
ENV_SECRET_STORE = "FORGEMASTER_SECRET_STORE"
ENV_COMPOSE_CMD = "FORGEMASTER_COMPOSE_CMD"
ENV_WS_ALLOWED_ORIGINS = "FORGEMASTER_WS_ALLOWED_ORIGINS"

DEFAULT_HOME = "~/.forgemaster"
DEFAULT_PROJECTS_ROOT = "~/projects"
DEFAULT_SECRET_STORE = "file"  # coffre par défaut : EncryptedFileStore (portable, zéro-config). Cf. secrets/.
DEFAULT_COMPOSE_CMD = "podman-compose"  # moteur de run (P2) : podman-compose standalone (portable Debian 12).


@dataclass(frozen=True)
class Settings:
    """Racines résolues du forgemaster (immuable). Construire via `Settings.resolve(...)`."""

    home: Path
    projects_root: Path
    secret_store: str = "file"  # sélecteur du coffre de credentials : "file" | "bws" (cf. secrets/).
    # Préfixe de commande du moteur compose (P2 runtime). Défaut `podman-compose` standalone : Debian 12 ne
    # package que podman 4.3.1, dépourvu de la sous-commande `podman compose` (≥4.4). `("docker","compose")`
    # via `FORGEMASTER_COMPOSE_CMD` reste un réglage (backend abstrait + engine-aware, cf.
    # runtime/backend.py).
    compose_cmd: tuple[str, ...] = ("podman-compose",)
    # Origines WS autorisées EN PLUS du same-origin (comparaison Origin↔Host, zéro-config) et du dev Vite
    # (cf. daemon/wsguard). Sert le cas « daemon derrière un reverse-proxy à nom public différent » : y
    # déposer l'origine publique (ex. "https://forgemaster.example"). Défaut vide : same-origin + dev couvrent
    # l'usage local/LAN. Env `FORGEMASTER_WS_ALLOWED_ORIGINS` (séparateur virgule/espace).
    ws_allowed_origins: tuple[str, ...] = ()
    # Porte du garde de schéma (`db.store.migrate`). Délibérément **sans** variable d'environnement et
    # **absente** de `resolve()` : c'est un laissez-passer d'UNE invocation, posé par la frontière CLI quand
    # `--allow-unknown-schema` est demandé. Le rendre configurable par l'env le rendrait *permanent*, et un
    # garde qu'on désactive une fois pour toutes sans s'en souvenir n'est plus un garde.
    allow_unknown_schema: bool = False

    @property
    def db_path(self) -> Path:
        """Chemin de la base SQLite unique (projects/features/tasks/dispatch_jobs)."""
        return self.home / "forgemaster.db"

    @property
    def logs_dir(self) -> Path:
        """Dossier des logs de workers dispatchés (un fichier par job)."""
        return self.home / "logs"

    @property
    def secrets_dir(self) -> Path:
        """Dossier du coffre fichier chiffré (clé-600 + blob) : `home/secrets/`. Cf. EncryptedFileStore."""
        return self.home / "secrets"

    @staticmethod
    def resolve(
        *,
        home: str | os.PathLike[str] | None = None,
        projects_root: str | os.PathLike[str] | None = None,
        secret_store: str | None = None,
        compose_cmd: str | None = None,
        ws_allowed_origins: str | None = None,
    ) -> Settings:
        """Résout les racines. Priorité par racine : argument explicite > variable d'env > défaut.
        `~` est toujours développé ; les chemins sont rendus absolus (jamais relatifs au cwd courant).
        `secret_store` est un sélecteur (chaîne, non normalisé), pas un chemin. `compose_cmd` est une
        chaîne (préfixe splité sur les espaces, ex. `"docker compose"`), normalisée en tuple.
        `ws_allowed_origins` est une liste d'origines (séparateur virgule/espace), normalisée en tuple."""
        h = _pick(home, os.environ.get(ENV_HOME), DEFAULT_HOME)
        p = _pick(projects_root, os.environ.get(ENV_PROJECTS_ROOT), DEFAULT_PROJECTS_ROOT)
        s = _pick(secret_store, os.environ.get(ENV_SECRET_STORE), DEFAULT_SECRET_STORE)
        c = _pick(compose_cmd, os.environ.get(ENV_COMPOSE_CMD), DEFAULT_COMPOSE_CMD)
        w = _pick(ws_allowed_origins, os.environ.get(ENV_WS_ALLOWED_ORIGINS), "")
        return Settings(home=_norm(h), projects_root=_norm(p), secret_store=s,
                        compose_cmd=tuple(c.split()),
                        ws_allowed_origins=tuple(o for o in re.split(r"[,\s]+", w) if o))


def _pick(explicit: object, env: str | None, default: str) -> str:
    """Premier non-vide parmi (explicite, env, défaut). L'explicite peut être un PathLike."""
    if explicit is not None and str(explicit) != "":
        return str(explicit)
    if env:
        return env
    return default


def _norm(raw: str) -> Path:
    """Développe `~` et rend absolu, sans exiger que le chemin existe (création paresseuse en aval)."""
    return Path(raw).expanduser().resolve()
