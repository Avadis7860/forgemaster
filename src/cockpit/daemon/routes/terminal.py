"""routes/terminal — router du terminal web (WebSocket → PTY LOCAL **détachable**). Ordre : **garde d'auth
d'abord** (hôte non authentifié → refus `1008` AVANT `accept()`, parité avec les routes mutantes — ce WS livre
un shell brut, aucun spawn silencieux), puis résout le workdir borné du projet (`pty.resolve_workdir`, #4
anti-traversal), **valide qu'il existe** avant `accept()` (sinon `bash -l` dans un dir absent crash
post-accept), puis délègue à `pty.serve_project_terminal` — qui ré-attache la session vivante du registre
(rejeu scrollback) ou en spawn une neuve. L'auth est **host** (par machine, comme tout le daemon) ; la
frontière réseau *client* (LAN/VPN) reste assurée hors process (cf. legacy)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, WebSocket

from cockpit import auth
from cockpit.terminal import pty


def make_terminal_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/terminal/{project}")
    async def terminal_ws(websocket: WebSocket, project: str) -> None:
        if not auth.claude_auth_status()["authenticated"]:
            await websocket.close(code=1008)   # hôte non authentifié → aucun shell (parité routes mutantes)
            return
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
