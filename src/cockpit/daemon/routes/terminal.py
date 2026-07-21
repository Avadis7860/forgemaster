"""routes/terminal — router du terminal web (WebSocket → PTY LOCAL **détachable**). Fin : résout le workdir
borné du projet (`pty.resolve_workdir`, #4 anti-traversal), **valide qu'il existe** avant `accept()` (sinon
`bash -l` dans un dir absent crash post-accept), puis délègue à `pty.serve_project_terminal` — qui ré-attache
la session vivante du registre (rejeu scrollback) ou en spawn une neuve. La frontière réseau (LAN/VPN) est
assurée hors process (cf. legacy). La garde d'auth du WS terminal est suivie à part (backlog)."""
from __future__ import annotations

from pathlib import Path

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
        if not Path(workdir).is_dir():                 # projet inexistant → refus AVANT accept (sinon
            await websocket.close(code=1008)           # Popen(cwd=absent) crash post-accept)
            return
        await websocket.accept()
        registry = websocket.app.state.terminals
        await pty.serve_project_terminal(websocket, registry, project=project,
                                         argv=pty.local_shell_argv(), cwd=workdir, env=pty.shell_env())

    return router
