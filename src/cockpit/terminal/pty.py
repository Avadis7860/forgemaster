"""pty — pont PTY ↔ WebSocket : ouvre un pseudo-terminal **LOCAL** dans le workdir d'un projet et relaie
octets + redimensionnements vers/depuis un client web (xterm.js).

Port de `services/aggregator/terminal.py` (`pty_bridge`, `parse_control` — transport-agnostiques). Refactors :
- **#2** : le legacy pilotait `ssh -tt dev@<CT>` (argv ssh) ; ici le PTY pilote un **login shell local**
  (`bash -l`) dans `cwd=workdir` — plus de clé ssh ni d'IP. `pty_bridge` reste agnostique (prend un argv +
  `cwd`), la couture ssh→local se réduit à `local_shell_argv`.
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
import os
import pty
import signal
import struct
import subprocess
import termios

from cockpit.config import Settings
from cockpit.core import fs

READ_SIZE = 65536


def local_shell_argv(shell: str = "/bin/bash") -> list[str]:
    """argv d'un **login shell local** (`bash -l`). PUR. Le cwd est posé par `pty_bridge` (Popen `cwd=`),
    pas par un `cd` embarqué — plus de couture ssh. `-l` charge le profil (PATH/nvm) comme un vrai term."""
    return [shell, "-l"]


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


def _terminate(proc: subprocess.Popen) -> None:
    """Tue le groupe de process du shell proprement (SIGTERM puis SIGKILL). Évite les zombies."""
    with contextlib.suppress(Exception):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


async def pty_bridge(websocket, argv: list[str], *, cwd: str | None = None,
                     env: dict | None = None) -> None:
    """Ouvre un PTY local pilotant `argv` (dans `cwd`), relaie PTY↔WebSocket jusqu'à la fin de l'un ou
    l'autre, puis nettoie. L'appelant a déjà `accept()` le WebSocket et validé la session. Agnostique au
    transport (le legacy passait un argv ssh ; ici un argv `bash -l` local — même corps)."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,  # noqa: S603
                            cwd=cwd, start_new_session=True, close_fds=True, env=env)
    os.close(slave)
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _on_readable() -> None:
        try:
            data = os.read(master, READ_SIZE)
        except OSError:
            data = b""                       # PTY fermé (process terminé) → EOF
        q.put_nowait(data)

    loop.add_reader(master, _on_readable)

    async def pty_to_ws() -> None:
        while True:
            data = await q.get()
            if not data:                     # b"" = EOF du PTY
                return
            await websocket.send_bytes(data)

    async def ws_to_pty() -> None:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                os.write(master, msg["bytes"])
            elif msg.get("text") is not None:
                size = parse_control(msg["text"])
                if size:
                    _set_winsize(master, *size)

    t1 = asyncio.create_task(pty_to_ws())
    t2 = asyncio.create_task(ws_to_pty())
    try:
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (t1, t2):
            t.cancel()
        loop.remove_reader(master)
        with contextlib.suppress(OSError):
            os.close(master)
        _terminate(proc)
        with contextlib.suppress(Exception):
            await websocket.close()
