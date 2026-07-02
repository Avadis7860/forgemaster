"""routes/dispatch — router de domaine « dispatch » (spawn worker + suivi de job). Fin : délègue à
`dispatch.worker` (gate no-task-no-dispatch inclus) et `dispatch.jobs` (état + suivi de log incrémental).

Le dispatch **spawn un `claude -p` local** qui peut tourner jusqu'à 30 min → il s'exécute dans un
**threadpool** (`run_in_threadpool`) pour ne pas bloquer la boucle asyncio. Le streaming live du transcript
(WebSocket `/ws/dispatch/{job}`) reste un concern web P5 ; ici on expose la primitive + le `tail` par pull."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from cockpit.daemon.deps import Deps, get_deps
from cockpit.dispatch import jobs, worker


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

    return router
