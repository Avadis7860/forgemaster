"""backend — l'interface `ComposeBackend` (le contrat que tout moteur compose honore) + l'adapter concret
`PodmanCompose` (défaut de l'édition publique). Calque exact de `git/backend.py` (Protocol figé) × la façon
dont `git/internal.py` enrobe `core.run` (idiome `_git`/`_checked` → ici `_compose`/`_checked`).

Le préfixe de commande vient de `Settings.compose_cmd` (défaut `("podman","compose")`) → basculer sur
`("docker","compose")` est un simple réglage, pas du code. Le **runner** (transport subprocess) est
**injecté** (défaut `core.run.run`) → les tests ne spawnent jamais un vrai conteneur (calque
`dispatch/worker.py`).
"""
from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from cockpit.core import run

DEFAULT_COMPOSE_CMD: tuple[str, ...] = ("podman", "compose")
COMPOSE_TIMEOUT = 600.0   # s ; un `up --build` peut être long (pull d'image + build) sans pendre l'appelant.

# Variables d'environnement du daemon **autorisées** à atteindre la CLI compose (allowlist, anti-pollution
# P4). Le strict nécessaire pour que `podman`/`docker compose` tourne — rootless inclus : PATH, HOME, le
# runtime XDG, la locale, les chemins de config containers/docker s'ils sont posés. JAMAIS un secret du
# daemon (`BWS_ACCESS_TOKEN`, `COCKPIT_*`, `GITHUB_TOKEN`…) : l'env de build/run d'un service est scopé par
# construction — il ne reçoit que cette base ⊕ l'overlay explicite de l'engine (`COCKPIT_PORT`,
# `COMPOSE_PROJECT_NAME`), jamais l'environnement du daemon hérité en bloc.
_COMPOSE_ENV_ALLOW: tuple[str, ...] = (
    "PATH", "HOME", "USER", "LOGNAME", "TERM", "TMPDIR",
    "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "LANG", "LC_ALL", "LC_CTYPE",
    "CONTAINERS_STORAGE_CONF", "CONTAINERS_REGISTRIES_CONF",   # podman rootless : config si posée
    "DOCKER_HOST", "DOCKER_CONFIG",                            # bascule docker compose : idem
)

# runner(argv, *, cwd, env, timeout) -> RunResult. Défaut = subprocess local ; injecté en test.
Runner = Callable[..., run.RunResult]


class ComposeError(RuntimeError):
    """Échec dur d'une commande compose (le message porte stderr). Les couches hautes le convertissent en
    `ValueError` (→ 400 route / `erreur` CLI) — calque de `GitOpError`."""


@runtime_checkable
class ComposeBackend(Protocol):
    """Contrat du moteur de run. Toute méthode opère sur UN compose-project (`project_name`, le namespace
    d'isolation) dans un `workdir` (qui porte le `compose.yaml`) et lève `ComposeError` sur échec dur."""

    def up(self, project_name: str, workdir: Path, *, env: Mapping[str, str] | None = None) -> None:
        """Build + démarre le service en arrière-plan (`up -d --build`). Idempotent (re-`up` reconverge)."""
        ...

    def down(self, project_name: str, workdir: Path, *, env: Mapping[str, str] | None = None) -> None:
        """Arrête + retire conteneurs/réseau du compose-project (`down`). Namespace nettoyé."""
        ...

    def restart(self, project_name: str, workdir: Path, *, env: Mapping[str, str] | None = None) -> None:
        """Redémarre les conteneurs existants (`restart`) — opère sur un déploiement `up` (après un `down`,
        ré-`up`)."""
        ...

    def ps(self, project_name: str, workdir: Path,
           *, env: Mapping[str, str] | None = None) -> list[dict]:
        """État des conteneurs du compose-project (`ps --format json`) — read-only, pour réconcilier le
        status. Liste vide = rien ne tourne."""
        ...

    def logs(self, project_name: str, workdir: Path, *, tail: int,
             env: Mapping[str, str] | None = None) -> list[str]:
        """Les `tail` dernières lignes de logs du compose-project (`logs --tail <n>`) — read-only, **borné**
        (jamais `--follow` : un one-shot, pas un flux long-vécu). Une ligne par entrée. Vide = rien à lire."""
        ...


def runtime_available(cmd: Sequence[str], *, path: str | None = None) -> bool:
    """True si le binaire du moteur compose (`cmd[0]`, podman/docker) **résout** sur le PATH — sonde pour
    `doctor` / preflight. Sans effet de bord (pas de sous-process). PUR (which)."""
    return bool(cmd) and shutil.which(cmd[0], path=path) is not None


def _default_runner(argv: Sequence[str], *, cwd: object, env: Mapping[str, str],
                    timeout: float) -> run.RunResult:
    # Preflight fail-GRACIEUX : le moteur (`podman`/`docker`) doit résoudre AVANT le sous-process, sinon
    # `subprocess` lève un `FileNotFoundError` **brut** (stacktrace) — on le convertit en `ComposeError`
    # actionnable, que les couches hautes dégradent proprement (status → unhealthy ; deploy → précondition
    # 400/CLI). On sonde le MÊME PATH que le run (env scellé), pas celui d'`os.environ`.
    if not runtime_available(argv, path=env.get("PATH")):
        raise ComposeError(
            f"runtime conteneur absent : `{argv[0]}` introuvable sur le PATH — installe podman (rootless, "
            "via `provision-ct.sh`) ou configure COCKPIT_COMPOSE_CMD=docker compose")
    return run.run(list(argv), cwd=cwd, env=env, timeout=timeout)   # type: ignore[arg-type]


class PodmanCompose:
    """Adapter `ComposeBackend` sur la CLI compose (`podman compose` par défaut, `docker compose` par
    réglage — même surface). Construit l'argv `<cmd> -p <name> <sous-commande>`, exécuté dans `workdir`."""

    def __init__(self, *, cmd: Sequence[str] = DEFAULT_COMPOSE_CMD, runner: Runner | None = None) -> None:
        self._cmd = tuple(cmd)
        self._run: Runner = runner or _default_runner

    def _base_env(self, env: Mapping[str, str] | None) -> dict[str, str]:
        """Env scellé (anti-pollution P4) : **allowlist stricte** de l'env du daemon (`_COMPOSE_ENV_ALLOW` :
        PATH/HOME/XDG… — le nécessaire pour que compose/podman tourne) ⊕ l'overlay explicite. AUCUN secret du
        daemon n'y fuit ; `core.run.run` **remplace** l'env, jamais hérité en bloc depuis `os.environ`."""
        base = {k: os.environ[k] for k in _COMPOSE_ENV_ALLOW if k in os.environ}
        return {**base, **(dict(env) if env else {})}

    def _compose(self, project_name: str, workdir: Path, *args: str,
                 env: Mapping[str, str] | None = None) -> run.RunResult:
        """Exécute `<cmd> -p <name> <args>` dans `workdir`. L'overlay (`COCKPIT_PORT`, `COMPOSE_PROJECT_NAME`)
        est requis pour l'interpolation `${VAR}` : le compose est **re-parsé à CHAQUE sous-commande** (up,
        down, restart, logs…) et son `ports:` est fail-loud (`${COCKPIT_PORT:?}`) → l'engine injecte toujours
        le port réservé. Env scellé par `_base_env` (allowlist P4)."""
        argv = [*self._cmd, "-p", project_name, *args]
        return self._run(argv, cwd=workdir, env=self._base_env(env), timeout=COMPOSE_TIMEOUT)

    def _checked(self, project_name: str, workdir: Path, *args: str,
                 env: Mapping[str, str] | None = None) -> run.RunResult:
        r = self._compose(project_name, workdir, *args, env=env)
        if not r.ok:
            raise ComposeError(f"compose {' '.join(args)} @ {project_name}: {r.stderr.strip()[:200]}")
        return r

    def up(self, project_name: str, workdir: Path, *, env: Mapping[str, str] | None = None) -> None:
        self._checked(project_name, workdir, "up", "-d", "--build", env=env)

    def down(self, project_name: str, workdir: Path, *, env: Mapping[str, str] | None = None) -> None:
        self._checked(project_name, workdir, "down", env=env)

    def restart(self, project_name: str, workdir: Path, *, env: Mapping[str, str] | None = None) -> None:
        self._checked(project_name, workdir, "restart", env=env)

    def ps(self, project_name: str, workdir: Path,
           *, env: Mapping[str, str] | None = None) -> list[dict]:
        # On interroge le moteur de conteneurs **directement** (`<engine> ps`), PAS `compose ps` :
        # podman-compose 1.0.6 n'accepte ni `--format json` ni `-a` pour `ps`, et re-parse le compose
        # (→ exige COCKPIT_PORT).
        # Le filtre par label `com.docker.compose.project` (posé par docker ET podman) donne l'état honnête
        # par conteneur, sans re-parse compose. Cross-backend (`self._cmd[0]` = podman|docker), env P4 scellé.
        argv = [self._cmd[0], "ps", "-a", "--format", "json",
                "--filter", f"label=com.docker.compose.project={project_name}"]
        r = self._run(argv, cwd=workdir, env=self._base_env(env), timeout=COMPOSE_TIMEOUT)
        if not r.ok:
            raise ComposeError(f"ps @ {project_name}: {r.stderr.strip()[:200]}")
        return _parse_ps(r.stdout)

    def logs(self, project_name: str, workdir: Path, *, tail: int,
             env: Mapping[str, str] | None = None) -> list[str]:
        # Comme `ps` : moteur DIRECT (`<engine> logs`), PAS `compose logs` — podman-compose n'écrit pas
        # fiablement les lignes sur stdout (+ pollue de sa bannière). On liste les conteneurs du projet (via
        # `ps`, par label) puis on tire leurs derniers logs, en lisant stdout ET stderr (un handler http logge
        # souvent sur stderr → `podman logs` le route sur notre stderr). Borné (`--tail`, jamais `--follow`).
        out: list[str] = []
        for c in self.ps(project_name, workdir, env=env):
            cid = str(c.get("Id") or c.get("ID") or "")       # podman: `Id` ; docker ndjson: `ID`
            if not cid:
                continue
            argv = [self._cmd[0], "logs", "--timestamps", "--tail", str(tail), cid]
            r = self._run(argv, cwd=workdir, env=self._base_env(env), timeout=COMPOSE_TIMEOUT)
            if not r.ok:
                raise ComposeError(f"logs @ {project_name}: {r.stderr.strip()[:200]}")
            out.extend((r.stdout + r.stderr).splitlines())     # lignes sur l'un OU l'autre flux
        return out[-tail:]                                     # borne finale (multi-conteneurs)


def _parse_ps(stdout: str) -> list[dict]:
    """Parse `compose ps --format json`, tolérant aux deux formes rencontrées : un **tableau JSON** (docker
    compose v2) ou du **NDJSON** (un objet par ligne, podman / anciennes versions). Vide/illisible → `[]`
    (best-effort ; la réconciliation de status n'est jamais un faux-vert — un `[]` lit « rien ne tourne »)."""
    text = stdout.strip()
    if not text:
        return []
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else [obj]
    except (ValueError, TypeError):
        rows: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows


def is_running(container: Mapping[str, object]) -> bool:
    """True si un conteneur `compose ps` est en marche. Tolère les clés docker (`State`) et podman
    (`State`/`Status`) : un `State` == `running` ou un `Status` commençant par `Up`/`running`."""
    state = str(container.get("State", "")).lower()
    status = str(container.get("Status", "")).lower()
    return state == "running" or status.startswith(("up", "running"))
