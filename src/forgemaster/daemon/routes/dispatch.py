"""routes/dispatch — router de domaine « dispatch » (spawn worker + suivi de job). Fin : délègue à
`dispatch.worker` (gate no-task-no-dispatch inclus), `dispatch.jobs` (état + suivi de log incrémental) et
`dispatch.stream` (pont transcript→WebSocket).

Le dispatch **spawn un `claude -p` local** qui peut tourner jusqu'à 30 min → il s'exécute dans un
**threadpool** (`run_in_threadpool`) pour ne pas bloquer la boucle asyncio, et **ne rend le `job_id` qu'à la
fin**. Le suivi **live** passe donc par la découverte du job en cours (`GET …/jobs`) puis le streaming
`WS /ws/dispatch/{job}` — qui boucle la primitive `jobs.read_events` (P5 Vague 3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from starlette.concurrency import run_in_threadpool

from forgemaster import auth, interview
from forgemaster.daemon.deps import Deps, get_deps
from forgemaster.daemon.wsguard import authorize_ws
from forgemaster.dispatch import abort, jobs, orchestrator, redrain, stream
from forgemaster.roadmap import model


def make_dispatch_router() -> APIRouter:
    router = APIRouter(tags=["dispatch"])

    @router.post("/api/dispatch/{project}/abort")
    async def abort_run(project: str, feature: str | None = None,
                        deps: Deps = Depends(get_deps)) -> dict:
        """**Arrête** le run en cours du projet (bouton « Arrêter le run ») : tue chaque worker par son pgid
        persisté (handle explicite — plus de `pkill` fragile), marque les jobs `killed` + tasks `todo`, pose
        la sentinelle que la boucle `run_feature`/`run_project` bloquée lit pour sortir → **re-runnable**,
        rien mergé. `feature` (query, optionnel) cible un seul worker. killpg + grâce = **bloquant** →
        threadpool. **Pas de gate d'auth** : arrêter doit TOUJOURS être possible (remède au footgun `pkill`).
        Déclaré AVANT `/{project}/{feature}` : sinon `/proj/abort` matcherait avec feature=='abort'."""
        return await run_in_threadpool(
            abort.request_abort, deps.settings, project=project, feature=feature)

    @router.post("/api/dispatch/{project}/reconcile-socle")
    async def reconcile_socle(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """**Valide l'interview & clôture le socle** (action nommée par son résultat, symétrique du bouton
        pré-interview) : réconcilie le socle du projet — clôt ses tasks + committe `design.md` — SANS
        relancer l'interview, et RAPPORTE ce qu'elle a fait (`status`, sha du design, N tasks closes,
        prochaine étape). Idempotent (re-jouable). Git = **bloquant** → threadpool. Pas de gate d'auth (aucun
        spawn `claude`, mutation locale idempotente — comme `abort`). Déclaré AVANT `/{project}/{feature}`
        (sinon `reconcile-socle` matcherait comme un slug de feature)."""
        def _run() -> dict:
            conn = deps.open_db()
            try:
                return interview.reconcile_socle_report(conn, deps.settings, project=project)
            finally:
                conn.close()
        return await run_in_threadpool(_run)

    @router.post("/api/dispatch/{project}/{feature}/redrain")
    async def redrain_route(project: str, feature: str, deps: Deps = Depends(get_deps)) -> dict:
        """**Re-draine** une feature à base périmée non-réalignable (recouvrement du conflit de fondations
        partagées, cf. le `GitOpError` de `rebase_onto`) : purge son worktree (branche réinitialisée sur `dev`
        au prochain dispatch via `add_worktree -B`), remet ses tasks `todo`, repasse la feature `planned`,
        résout ses alertes stale. **Idempotent**, `dev`/`main` intouchés. **Pas de gate d'auth** (aucun spawn
        `claude`, mutation locale — comme `abort`/`reconcile-socle`). Git = bloquant → threadpool. Déclarée
        AVANT `/{project}/{feature}` par cohérence de lecture. Feature absente → `KeyError` → 404."""
        def _run() -> dict:
            conn = deps.open_db()
            try:
                return redrain.redrain_feature(conn, deps.settings, project=project, feature=feature)
            finally:
                conn.close()
        return await run_in_threadpool(_run)

    @router.post("/api/dispatch/{project}/{feature}")
    async def dispatch(project: str, feature: str, deps: Deps = Depends(get_deps)) -> dict:
        """**Draine** la feature (DAG intra-feature, séquentiel) PUIS la **finalise** (Tier-0 + review Tier-1
        → gate preview) — symétrique du CLI (`forgemaster run`), via `orchestrator.run_feature`. La review est
        donc produite SANS clic (le POST bloque jusqu'au bout ; suivi live par `GET …/jobs` + WS). Spawn(s)
        `claude -p` longs → threadpool. Gate d'auth (jamais de spawn silencieux). Retour = rapport agrégé
        (même forme que le CLI)."""
        if not auth.claude_auth_status()["authenticated"]:
            raise HTTPException(status_code=403, detail=auth.AUTH_HINT)   # jamais de spawn silencieux
        def _run() -> dict:
            conn = deps.open_db()
            try:
                return orchestrator.run_feature(conn, deps.settings, project=project, feature=feature)
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
        """Transcript **live** d'un job : garde CSWSH (Origin + token, `wsguard`) PUIS validation d'existence,
        le tout AVANT `accept()` (job inconnu → refus policy 1008, jamais un flux vide ; garde d'abord pour ne
        pas offrir d'oracle d'existence à un handshake non autorisé), puis délègue à `stream.stream_job`."""
        deps: Deps = websocket.app.state.deps
        subprotocol = await authorize_ws(websocket)     # Origin + token AVANT tout accept (et avant l'oracle)
        if subprotocol is None:
            return
        conn = deps.open_db()
        try:
            jobs.get_job(conn, job_id)                  # KeyError si inconnu
        except KeyError:
            await websocket.close(code=1008)
            return
        finally:
            conn.close()
        await websocket.accept(subprotocol=subprotocol)  # echo du token retenu (obligation RFC 6455)
        await stream.stream_job(websocket, deps, job_id)

    return router
