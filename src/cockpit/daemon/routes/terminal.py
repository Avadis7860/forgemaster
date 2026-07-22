"""routes/terminal — router du terminal web (WebSocket → PTY LOCAL **détachable**). Ordre : résout le
workdir borné du projet (`pty.resolve_workdir`, #4 anti-traversal), **valide qu'il existe** avant `accept()`
(sinon `bash -l` dans un dir absent crash post-accept), puis délègue à `pty.serve_project_terminal` — qui
ré-attache la session vivante du registre (rejeu scrollback) ou en spawn une neuve.

**Pas de gate `claude_auth` ici** (retiré) : ce WS ouvre un **shell local** (`bash -l`), il ne **spawne
jamais** un worker `claude` — c'est justement la surface où l'utilisateur lance son `claude login`
d'onboarding (cf. `auth.AUTH_HINT` / `CLAUDE_LOGIN_HINT` : « dans le terminal »). Le gater sur l'auth Claude
déjà présente était une **erreur de catégorie** : deadlock poule-et-œuf (login impossible car pas encore
loggé). Le gate d'auth reste sur les **chemins de spawn réels** (`dispatch`/`orchestrator`/`worker`/
`reviewer`/`interview` + routes `dispatch`/`gate`), pas ici. La frontière **client** (qui peut joindre ce
shell) reste la frontière **réseau** (LAN/VPN), assurée hors process — l'auth host ne l'a jamais fournie."""
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
