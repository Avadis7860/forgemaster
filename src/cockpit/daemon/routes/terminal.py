"""routes/terminal — router du terminal web (WebSocket → PTY LOCAL). Fin : résout le workdir borné du
projet (`pty.resolve_workdir`, #4 anti-traversal) et relaie via `terminal.pty.pty_bridge` un login shell
local (`bash -l`, #2 plus de ssh). La frontière réseau (LAN/VPN) est assurée hors process (cf. legacy)."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket

from cockpit.terminal import pty


def make_terminal_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/terminal/{project}")
    async def terminal_ws(websocket: WebSocket, project: str) -> None:
        deps = websocket.app.state.deps
        try:
            workdir = pty.resolve_workdir(deps.settings, project)   # borné au dossier du projet
        except ValueError:
            await websocket.close(code=1008)                        # chemin hors racine → refus policy
            return
        await websocket.accept()
        await pty.pty_bridge(websocket, pty.local_shell_argv(), cwd=workdir)

    return router
