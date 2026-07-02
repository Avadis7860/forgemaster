"""stream — pont transcript→WebSocket : pousse en live les événements normalisés d'un job de dispatch.

Analogue de `terminal.pty` côté dispatch : le router reste fin (valide le job + `accept()`), ici on boucle la
primitive **pure** `jobs.read_events` (offset/inode persistés → robuste rotation / ligne partielle) et on émet
chaque événement conversationnel en frame JSON. On sonde le statut du job en DB pour clore quand il devient
terminal (`done|failed|killed`). L'ordre **lire-puis-tester** garantit un dernier drain : le worker écrit
toutes ses lignes AVANT que `record_finish` ne pose le statut terminal, donc l'itération qui voit le statut
terminal a déjà relu la fin du transcript. Pas de `tail -F`, pas de sous-shell.
"""
from __future__ import annotations

import asyncio
import contextlib

from cockpit.daemon.deps import Deps
from cockpit.dispatch import jobs

_TERMINAL = {"done", "failed", "killed"}
POLL_S = 0.4


async def stream_job(websocket, deps: Deps, job_id: str, *, poll_s: float = POLL_S) -> None:
    """Boucle live d'un job : lit le transcript incrémentalement, pousse les events normalisés, s'arrête au
    statut terminal (émet une frame `{"type":"job",...}` finale). L'appelant a déjà `accept()` le WebSocket et
    validé l'existence du job. Toute erreur transport (client déconnecté, socket fermée) sort proprement."""
    offset, inode = 0, None
    with contextlib.suppress(Exception):     # déconnexion client / socket fermée → sortie propre
        while True:
            conn = deps.open_db()
            try:
                job = jobs.get_job(conn, job_id)
            except KeyError:
                break                        # job supprimé sous nos pieds → fin de flux
            finally:
                conn.close()

            log_path = job.get("log_path")
            if log_path:                     # lecture AVANT test terminal → dernier drain garanti
                res = jobs.read_events(log_path, offset=offset, inode=inode)
                offset, inode = res["offset"], res["inode"]
                for ev in res["events"]:
                    await websocket.send_json(ev)

            if job.get("status") in _TERMINAL:
                await websocket.send_json({
                    "type": "job", "status": job["status"], "num_turns": job.get("num_turns"),
                    "cost_usd": job.get("cost_usd"), "wall_s": job.get("wall_s"),
                })
                break
            await asyncio.sleep(poll_s)
    with contextlib.suppress(Exception):
        await websocket.close()
