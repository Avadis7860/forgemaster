"""routes/terminal — router des sessions PTY web **détachables** (WebSocket → PTY LOCAL). Deux flavors,
même garde, **clés de registre distinctes** (aucune collision) :

- `/ws/terminal/{project}` → **shell** de projet (`bash -l`, clé `<project>`). C'est la surface où
  l'utilisateur lance son `claude login` d'onboarding — **pas de gate `claude_auth`** ici (le shell ne spawne
  pas de worker `claude` ; gater serait un deadlock poule-et-œuf, cf. `auth.AUTH_HINT`). Le gate d'auth reste
  sur les chemins de spawn réels (`dispatch`/`orchestrator`/`worker`/`reviewer`/`interview` CLI + routes).
- `/ws/interview/{project}` → **interview de socle** (`bash -lc 'exec cockpit interview <project>'`, clé
  `interview:<project>`). Le process du PTY **EST** l'interview (plus de commande tapée dans un shell partagé
  gaté sur `fresh` — l'ancien modèle cassait dès que le shell du projet persistait). `cockpit interview`
  regate l'auth côté CLI et l'affiche dans le PTY. Session **distincte** du shell → persistance/reprise
  propres, indépendantes.

Garde commune (avant `accept()`) : `pty.resolve_workdir` (#4 anti-traversal) + `is_dir` (sinon `bash` dans un
dir absent crash post-accept). La frontière **client** reste la frontière **réseau** (LAN/VPN), hors
process."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, WebSocket

from cockpit.provision.mcp import inject_mcp_config
from cockpit.terminal import pty


async def _accept_project_pty(websocket: WebSocket, project: str) -> str | None:
    """Garde partagée : résout le workdir borné du projet, câble le MCP de corpus dans ce cwd, et `accept()`
    le WS s'il existe. Retourne le workdir (accepté), ou `None` après avoir fermé le WS (`1008`) sur traversal
    / projet inexistant — refus AVANT `accept()` pour ne jamais `Popen(cwd=absent)`.

    Injection MCP (miroir du dispatch headless `worker._run_worker`) : un `.mcp.json` frais (JWT minté
    just-in-time, chmod 600, gitignoré) est écrit au cwd du PTY → un `claude` lancé dans le terminal
    DÉCOUVRE le MCP `mcp-catalogs` (`/mcp` le liste, solve-mode disponible pour un travail jamais fait).
    No-op honnête si le MCP n'est pas câblé (install sans corpus privé). Le cwd n'est pas un working-tree
    (racine projet = `sot.git` bare + `worktrees/`) → le Bearer n'est jamais committé."""
    deps = websocket.app.state.deps
    try:
        workdir = pty.resolve_workdir(deps.settings, project)   # borné au dossier du projet
    except ValueError:
        await websocket.close(code=1008)                        # chemin hors racine → refus policy
        return None
    if not Path(workdir).is_dir():                              # projet inexistant → refus AVANT accept
        await websocket.close(code=1008)
        return None
    inject_mcp_config(Path(workdir), deps.settings, slug=project)   # câble le MCP au cwd (no-op si non câblé)
    await websocket.accept()
    return workdir


def make_terminal_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/terminal/{project}")
    async def terminal_ws(websocket: WebSocket, project: str) -> None:
        workdir = await _accept_project_pty(websocket, project)
        if workdir is None:
            return
        registry = websocket.app.state.terminals
        await pty.serve_project_terminal(websocket, registry, session_key=project,
                                         argv=pty.local_shell_argv(), cwd=workdir, env=pty.shell_env())

    @router.websocket("/ws/interview/{project}")
    async def interview_ws(websocket: WebSocket, project: str) -> None:
        workdir = await _accept_project_pty(websocket, project)
        if workdir is None:
            return
        registry = websocket.app.state.terminals
        await pty.serve_project_terminal(websocket, registry, session_key=f"interview:{project}",
                                         argv=pty.interview_argv(project), cwd=workdir, env=pty.shell_env())

    return router
