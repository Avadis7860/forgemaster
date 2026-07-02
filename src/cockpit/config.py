"""config — le SOCLE : résolveur générique des racines du cockpit. Aucune notion de vault, proxmox, CT
ou ssh (correctif : le legacy `server.py` codait `SSH_KEY_PATH`, `resolve_ctid`, `/home/dev` en dur).

Deux racines, résolues indépendamment par priorité **override explicite > env > défaut** :
- `home` — l'état du cockpit (base SQLite, logs de jobs). `COCKPIT_HOME`, défaut `~/.cockpit`.
- `projects_root` — où vivent les repos des projets gérés (SoT bare + worktrees). `COCKPIT_PROJECTS_ROOT`,
  défaut `~/projects`.

`Settings` est **gelé** (immuable) et se dérive une fois au démarrage (CLI/daemon), puis se passe
explicitement aux couches — jamais un module-global mutable (correctif anti god-module `import server`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "COCKPIT_HOME"
ENV_PROJECTS_ROOT = "COCKPIT_PROJECTS_ROOT"

DEFAULT_HOME = "~/.cockpit"
DEFAULT_PROJECTS_ROOT = "~/projects"


@dataclass(frozen=True)
class Settings:
    """Racines résolues du cockpit (immuable). Construire via `Settings.resolve(...)`."""

    home: Path
    projects_root: Path

    @property
    def db_path(self) -> Path:
        """Chemin de la base SQLite unique (projects/features/tasks/dispatch_jobs)."""
        return self.home / "cockpit.db"

    @property
    def logs_dir(self) -> Path:
        """Dossier des logs de workers dispatchés (un fichier par job)."""
        return self.home / "logs"

    @staticmethod
    def resolve(
        *,
        home: str | os.PathLike[str] | None = None,
        projects_root: str | os.PathLike[str] | None = None,
    ) -> Settings:
        """Résout les racines. Priorité par racine : argument explicite > variable d'env > défaut.
        `~` est toujours développé ; les chemins sont rendus absolus (jamais relatifs au cwd courant)."""
        h = _pick(home, os.environ.get(ENV_HOME), DEFAULT_HOME)
        p = _pick(projects_root, os.environ.get(ENV_PROJECTS_ROOT), DEFAULT_PROJECTS_ROOT)
        return Settings(home=_norm(h), projects_root=_norm(p))


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
