"""routes/gate — router de domaine « gate » (verdict Tier-1 review, statut des gates, merge sous GO humain).
Fin : délègue à `gate.review` / `gate.verify` / `gate.merge` (chaîne d'autorité) et résout le SHA d'ancrage +
le diff via `git.internal` (le verdict est lié au SHA de la branche de feature)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from cockpit import auth
from cockpit.daemon.deps import Deps, get_deps
from cockpit.dispatch import reviewer, worktree
from cockpit.gate import merge, review, toolchain
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects.registry import get_project
from cockpit.roadmap.model import resolve_feature
from cockpit.tools import tools_env


class ReviewBody(BaseModel):
    findings: list[dict] = []
    base: str = "dev"


class MergeBody(BaseModel):
    go: bool = False
    t1_override: str = ""
    t15_override: str = ""


def _resolve_sot_and_branch(deps: Deps, project: str, feature: str) -> tuple[Path, str]:
    conn = deps.open_db()
    try:
        feat = resolve_feature(conn, f"{project}/{feature}")   # KeyError/ValueError → handler global
        sot = Path(get_project(conn, project)["sot_path"])
    finally:
        conn.close()
    return sot, feat["branch"]


def make_gate_router() -> APIRouter:
    router = APIRouter(tags=["gate"])

    @router.post("/api/gate/{project}/{feature}/review", status_code=201)
    def write_review(project: str, feature: str, body: ReviewBody,
                     deps: Deps = Depends(get_deps)) -> dict:
        """Écrit le verdict Tier-1 SHA-bound. Fail-closed : branche/diff absents ou diff vide → 422 (on
        n'écrit JAMAIS un verdict non ancré/non validé)."""
        sot, branch = _resolve_sot_and_branch(deps, project, feature)
        git = InternalGit()
        try:
            sha = git.feature_sha(sot, branch)
            diff_text = git.diff_text(sot, base=body.base, head=branch)
        except GitOpError as exc:
            raise HTTPException(status_code=422, detail=f"branche/diff introuvable : {exc}") from exc
        if not diff_text.strip():
            raise HTTPException(status_code=422, detail=f"diff {body.base}...{branch} vide — rien à valider")
        conn = deps.open_db()                                # conn ⇒ verdict archivé par SHA (best-effort)
        try:
            return review.write_verdict(deps.settings, project, feature,
                                        {"findings": body.findings, "base": body.base},
                                        sha=sha, diff_text=diff_text, conn=conn)
        finally:
            conn.close()

    @router.post("/api/gate/{project}/{feature}/toolchain", status_code=201)
    def run_toolchain_gate(project: str, feature: str, deps: Deps = Depends(get_deps)) -> dict:
        """Exécute la toolchain Tier-0 native (front/backend selon le diff) dans le worktree de la feature et
        écrit le verdict SHA-bound. Action EXPLICITE (jamais un poll — miroir du POST …/review) : c'est un
        step de gate, pas le GET idempotent. Fail-closed : worktree/branche absents → 422 (rien écrit)."""
        sot, branch = _resolve_sot_and_branch(deps, project, feature)
        wt = worktree.worktree_path_for(deps.settings, project, feature)
        if not wt.is_dir():
            raise HTTPException(status_code=422, detail=f"worktree absent : {wt} — feature non dispatchée")
        git = InternalGit()
        try:
            sha = git.feature_sha(sot, branch)
            diff_files = git.diff_names(sot, base="dev", head=branch)
        except GitOpError as exc:
            raise HTTPException(status_code=422, detail=f"branche/diff introuvable : {exc}") from exc
        # env=tools_env : PATH natif (ruff/mypy/npm) — ALIGNÉ sur la CLI et l'orchestrator
        results = toolchain.run_toolchain(wt, diff_files, env=tools_env(deps.settings))
        conn = deps.open_db()                                # conn ⇒ verdict archivé par SHA (best-effort)
        try:
            return toolchain.write_verdict(deps.settings, project, feature, results, sha=sha, conn=conn)
        finally:
            conn.close()

    @router.post("/api/gate/{project}/{feature}/review-dispatch", status_code=201)
    async def dispatch_review(project: str, feature: str, deps: Deps = Depends(get_deps)) -> dict:
        """**DISPATCHE** le review-worker Tier-1 (`claude -p` read-only) sur la feature complète → écrit le
        verdict SHA-bound. C'est le bouton « lancer une review » : il **produit** le verdict que le gate de
        merge attend (le POST …/review, lui, *ingère* un verdict tout fait). Readiness-gate + idempotent dans
        `reviewer.dispatch_reviewer`. Spawn long → threadpool. Gate d'auth (jamais de spawn silencieux)."""
        if not auth.claude_auth_status()["authenticated"]:
            raise HTTPException(status_code=403, detail=auth.AUTH_HINT)

        def _run() -> dict:
            conn = deps.open_db()
            try:
                return reviewer.dispatch_reviewer(conn, deps.settings, feature_ref=f"{project}/{feature}")
            finally:
                conn.close()
        return await run_in_threadpool(_run)

    @router.get("/api/gate/{project}/{feature}")
    def gate_status(project: str, feature: str, deps: Deps = Depends(get_deps)) -> dict:
        """Vue Gate ancrée sur le SHA courant : le statut BRUT (review Tier-1 + verify Tier-1.5) ET la
        **décision composée** en *preview GO=false* (`decision`: hold/merge, `gate_green`, `blockers`,
        `reasons`, overrides), via `merge.evaluate_gate` — **sans jamais muter**. Le front rend « gate vert
        sans GO » depuis ce seul GET idempotent (le POST /api/merge reste réservé au vrai GO humain, et le
        runner de boucle visuelle *goto-only* ne POSTe donc rien). Branche jamais créée → `head_sha=None`,
        `decision=None` (verdicts « absents »)."""
        conn = deps.open_db()
        try:
            ev = merge.evaluate_gate(conn, deps.settings, feature_ref=f"{project}/{feature}", human_go=False)
        finally:
            conn.close()
        return {"head_sha": ev["head_sha"], "ui_touched": ev["ui_touched"],
                "review": ev["tier1_status"], "verify": ev["t15_status"], "decision": ev["decision"]}

    @router.post("/api/merge/{project}/{feature}")
    def do_merge(project: str, feature: str, body: MergeBody,
                 deps: Deps = Depends(get_deps)) -> dict:
        """Merge sous GO humain (`go`). Sans `go`, un gate vert renvoie `merged=false` / `hold` (bouton
        actif). Compose la chaîne d'autorité (`run_merge`), internal-first."""
        conn = deps.open_db()
        try:
            return merge.run_merge(conn, deps.settings, feature_ref=f"{project}/{feature}",
                                   human_go=body.go, t1_override=body.t1_override,
                                   t15_override=body.t15_override)
        finally:
            conn.close()

    return router
