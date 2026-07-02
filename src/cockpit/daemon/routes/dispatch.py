"""routes/dispatch — router de domaine « dispatch » (spawn worker + suivi de job). Fin : délègue à
`dispatch.worker` (gate no-task-no-dispatch inclus), `dispatch.jobs` (état + suivi de log incrémental) et
`dispatch.stream` (pont transcript→WebSocket).

Le dispatch **spawn un `claude -p` local** qui peut tourner jusqu'à 30 min → il s'exécute dans un
**threadpool** (`run_in_threadpool`) pour ne pas bloquer la boucle asyncio, et **ne rend le `job_id` qu'à la
fin**. Le suivi **live** passe donc par la découverte du job en cours (`GET …/jobs`) puis le streaming
`WS /ws/dispatch/{job}` — qui boucle la primitive `jobs.read_events` (P5 Vague 3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket
from starlette.concurrency import run_in_threadpool

from cockpit.daemon.deps import Deps, get_deps
from cockpit.dispatch import jobs, stream, worker
from cockpit.roadmap import model


def make_dispatch_router() -> APIRouter:
    router = APIRouter(tags=["dispatch"])

    @router.post("/api/dispatch/{project}/{feature}")
    async def dispatch(project: str, feature: str, deps: Deps = Depends(get_deps)) -> dict:
        def _run() -> dict:
            conn = deps.open_db()
            try:
                return worker.dispatch_next(conn, deps.settings, feature_ref=f"{project}/{feature}")
            finally:
                conn.close()
        return await run_in_threadpool(_run)

    @router.get("/api/jobs/{job_id}")
    def job(job_id: str, deps: Deps = Depends(get_deps)) -> dict:
        conn = deps.open_db()
        try:
            j = jobs.get_job(conn, job_id)
            if j is None:
                raise KeyError(job_id)                  # → 404 (handler global)
            return {"job": j, "events": jobs.tail(conn, job_id)}
        finally:
            conn.close()

    @router.get("/api/dispatch/{project}/{feature}/jobs")
    def feature_jobs(project: str, feature: str, deps: Deps = Depends(get_deps)) -> dict:
        """Jobs d'une feature (récents d'abord). Le front y **découvre** le job à streamer (le POST dispatch
        bloque jusqu'à la fin) et lit l'historique des runs. Feature absente → `KeyError` → 404."""
        conn = deps.open_db()
        try:
            feat = model.resolve_feature(conn, f"{project}/{feature}")
            return {"jobs": jobs.list_jobs(conn, feat["id"])}
        finally:
            conn.close()

    @router.websocket("/ws/dispatch/{job_id}")
    async def dispatch_ws(websocket: WebSocket, job_id: str) -> None:
        """Transcript **live** d'un job : valide l'existence AVANT `accept()` (job inconnu → refus policy
        1008, jamais un flux vide), puis délègue la boucle de streaming à `stream.stream_job`."""
        deps: Deps = websocket.app.state.deps
        conn = deps.open_db()
        try:
            jobs.get_job(conn, job_id)                  # KeyError si inconnu
        except KeyError:
            await websocket.close(code=1008)
            return
        finally:
            conn.close()
        await websocket.accept()
        await stream.stream_job(websocket, deps, job_id)

    return router
