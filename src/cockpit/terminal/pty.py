"""pty — service PTY ↔ WebSocket : sert un pseudo-terminal **LOCAL** dans le workdir d'un projet et relaie
octets + redimensionnements vers/depuis un client web (xterm.js). Les sessions sont **détachables** — le
shell survit à la déconnexion du WS ; leur cycle de vie vit dans `terminal.registry`.

Port de `services/aggregator/terminal.py` (`pty_bridge`, `parse_control` — transport-agnostiques). Refactors :
- **#2** : le legacy pilotait `ssh -tt dev@<CT>` (argv ssh) ; ici le PTY pilote un **login shell local**
  (`bash -l`) dans `cwd=workdir` — plus de clé ssh ni d'IP. `serve_project_terminal` reste agnostique
  (prend un argv + `cwd`), la couture ssh→local se réduit à `local_shell_argv`.
- **#4** : le workdir est borné par `core.fs.safe_path(root=<dir du projet>)` (plus de `/home/dev` en dur).

Convention WebSocket (inchangée) : frames BINAIRES = frappes → écrites telles quelles dans le PTY ; frames
TEXTE = contrôle JSON, seul `{"type":"resize","cols":C,"rows":R}` est traité (TIOCSWINSZ). La gate de
session + l'audit open/close sont à la charge de l'appelant (le router), AVANT `accept()`.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import shlex
import struct
import termios

from cockpit.config import Settings
from cockpit.core import fs
from cockpit.terminal.registry import PtySession, PtySessionRegistry


def local_shell_argv(shell: str = "/bin/bash") -> list[str]:
    """argv d'un **login shell local** (`bash -l`). PUR. Le cwd est posé par `PtySession.spawn` (Popen
    `cwd=`), pas par un `cd` embarqué — plus de couture ssh. `-l` charge le profil (PATH/nvm) comme un
    vrai term."""
    return [shell, "-l"]


def interview_argv(project: str, shell: str = "/bin/bash") -> list[str]:
    """argv d'une **session PTY dont le process EST l'interview de socle** (pas une commande tapée dans un
    shell partagé). PUR. `bash -lc 'exec cockpit interview <project>'` : le `-l` charge le PATH (cockpit via
    `/etc/profile.d/cockpit-path.sh`, cf. `shell_env`), puis `exec` **remplace** le shell par l'interview →
    aucun prompt où une frappe pourrait se ré-router, et à la sortie de l'interview le PTY reçoit l'EOF → la
    session se ferme d'elle-même. `cockpit interview` porte tout le cycle (worktree reserve → `claude`
    interactif TTY-hérité → reconcile) et regate l'auth Claude côté CLI (affichée dans le PTY)."""
    return [shell, "-lc", f"exec cockpit interview {shlex.quote(project)}"]


def shell_env() -> dict[str, str]:
    """Environnement du shell PTY : hérite du process **et force un terminal COULEUR**. Un service systemd
    n'a pas de `TERM` dans son env → sans ça bash/ls/git/less rendent monochrome ; xterm.js EST un
    `xterm-256color`, `COLORTERM=truecolor` débloque la 24-bit. **Le PATH n'est PAS posé ici** : `bash -l`
    (login shell) source `/etc/profile` qui RÉINITIALISE `PATH` (root → défaut Debian), écrasant tout PATH
    injecté. Le toolchain (`cockpit` + maps + `node`) est ré-ajouté APRÈS ce reset par
    `/etc/profile.d/cockpit-path.sh` que `deploy/provision-ct.sh` installe (le bon étage : le profil, pas le
    daemon)."""
    return {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}


def resolve_workdir(settings: Settings, project: str, subpath: str | None = None) -> str:
    """Workdir d'un terminal de projet, **borné** au dossier du projet (`<projects_root>/<project>`) via
    `fs.safe_path` (#4, anti-traversal). `subpath` relatif → résolu sous la racine ; tout `..` qui sort →
    `ValueError`. Défaut = la racine du projet (qui contient `sot.git` + `worktrees/`)."""
    root = str(settings.projects_root / project)
    return fs.safe_path(subpath, root=root)


def parse_control(text: str) -> tuple[int, int] | None:
    """Parse un message de contrôle TEXTE → (rows, cols) si c'est un resize valide, sinon None. PUR."""
    try:
        ctl = json.loads(text)
    except (ValueError, TypeError):                      # texte non-JSON = pas un contrôle
        return None
    if not isinstance(ctl, dict) or ctl.get("type") != "resize":
        return None
    try:
        cols = max(1, min(500, int(ctl["cols"])))
        rows = max(1, min(300, int(ctl["rows"])))
    except (KeyError, TypeError, ValueError):
        return None
    return rows, cols


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


# Motifs de sortie du relais : d'où vient la fin de session (décide détacher vs teardown).
_DISCONNECT = "disconnect"   # le client WS est parti → détacher (le shell survit)
_EOF = "eof"                 # le shell a fermé le PTY → teardown final
_REPLACED = "replaced"       # un nouveau client a pris la session → sortir sans y toucher


def classify_exit(code: int | None) -> str:
    """Classe le code de sortie du shell d'interview en RAISON portée à l'UI. PUR. Le PTY d'interview lance
    `bash -lc 'exec cockpit interview <p>'` : un `exec` échoué (`cockpit` introuvable / non exécutable → PATH
    de login non câblé) fait sortir bash en **127/126** ; une sortie propre de l'interview → **0** ; tout
    autre code (ou process tué sans code, `None`) → un **crash** en cours d'exécution. `failed_start` et
    `crash` mènent tous deux à une branche d'erreur *technique* côté UI (distincte du cadrage métier « pas de
    roadmap »), mais restent nommés séparément pour un wording futur plus fin."""
    if code == 0:
        return "clean"
    if code in (126, 127):
        return "failed_start"
    return "crash"


async def serve_project_terminal(websocket, registry: PtySessionRegistry, *, session_key: str,
                                 argv: list[str], cwd: str | None, env: dict | None) -> None:
    """Sert une session PTY **détachable**, indexée par `session_key` (le SEUL identifiant de registre — la
    bannière est côté client). Réutilise la session vivante du registre (ré-attache + rejeu du scrollback) ou
    en spawn une neuve avec `argv`. Le WebSocket est déjà `accept()` par le router. À la déconnexion du client
    alors que le process VIT, on **détache** (il continue, le reaper le TTL-e) au lieu de le tuer — la feature
    même. Sur EOF réel (process sorti), teardown final + retrait du registre. Annonce d'abord une frame de
    contrôle `{"t":"session","fresh":…}` : `fresh` distingue une session NEUVE d'une ré-attache (le client
    n'en fait qu'une bannière). Chaque flavor (shell `bash -l` du projet, interview `cockpit interview`) a sa
    propre `session_key` → sessions distinctes, persistance et fraîcheur indépendantes, aucune collision."""
    session = registry.get(session_key)
    if session is not None and not session.alive():
        session.close(registry.killer)                       # session morte résiduelle → on repart propre
        registry.pop(session_key, session)
        session = None
    fresh = session is None
    if session is None:
        session = PtySession.spawn(session_key, argv, cwd=cwd, env=env, clock=registry.clock)
        registry.put(session_key, session)
    epoch, cursor = session.attach()

    with contextlib.suppress(Exception):                     # contrôle TEXTE (bytes serveur→client = PTY)
        await websocket.send_text(json.dumps({"t": "session", "fresh": fresh}))

    reason = await _relay(websocket, session, epoch, cursor)
    if reason == _REPLACED:
        return                                               # un nouveau client possède la session
    if reason == _DISCONNECT and session.alive():
        session.detach()                                     # le process survit ; le reaper s'en chargera
        return
    session.close(registry.killer)                           # EOF / process mort → teardown final (reap)
    registry.pop(session_key, session)
    if reason == _EOF:                                       # le process a fini, un client regardait
        # Frame de contrôle finale porteuse de la RAISON de sortie du PTY (contrat WS, cf. CHANGELOG) : l'UI
        # distingue un échec technique (failed_start/crash → danger) d'une sortie propre (clean). Émise avant
        # le close, sur EOF réel SEULEMENT — un disconnect (client parti) n'a pas de spectateur à qui rendre
        # un verdict. Générique (tout terminal) : le front l'ignore là où elle n'est pas consommée.
        code = session.exit_code
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps({"t": "exit", "code": code, "reason": classify_exit(code)}))
    with contextlib.suppress(Exception):
        await websocket.close()


async def _relay(websocket, session: PtySession, epoch: int, cursor: int) -> str:
    """Relais bidirectionnel PTY↔WS jusqu'à la fin de l'un des sens. Retourne le motif de sortie
    (`disconnect`/`eof`/`replaced`). Le sens PTY→WS lit le scrollback par **curseur** (rejeu à l'attache puis
    live), sort en `replaced` si l'epoch a changé (nouveau client) ou en `eof` quand le shell a fini.
    Le sens WS→PTY sort en `disconnect`. Une exception de transport (client coupé net) est traitée comme une
    déconnexion (loggée, jamais avalée — durcissement)."""
    async def pty_to_ws() -> str:
        c = cursor
        while True:
            await session.data_event.wait()
            if session.epoch != epoch:                       # un attach plus récent nous a remplacés
                return _REPLACED
            session.data_event.clear()                       # clear AVANT lecture : pas de réveil perdu
            chunk, total = session.read_from(c)
            if chunk:
                await websocket.send_bytes(chunk)
                c = total
            if session.eof and c >= session.total:
                return _EOF

    async def ws_to_pty() -> str:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return _DISCONNECT
            if msg.get("bytes") is not None:
                os.write(session.master, msg["bytes"])
            elif msg.get("text") is not None:
                size = parse_control(msg["text"])
                if size:
                    _set_winsize(session.master, *size)

    t1 = asyncio.create_task(pty_to_ws())
    t2 = asyncio.create_task(ws_to_pty())
    done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    finished = done.pop()
    exc = finished.exception()
    if exc is not None:                                      # transport cassé côté client → déconnexion
        logging.getLogger("cockpit").info("relais terminal interrompu (%s) → détache", type(exc).__name__)
        return _DISCONNECT
    return finished.result()
