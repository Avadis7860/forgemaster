"""routes/reliability — router de domaine « fiabilité du gate vert » (enabler auto-merge, v18).

Lecture PURE du taux de fiabilité (`db/merge_outcomes.reliability`) — par projet ou global — et MARQUE de
l'issue aval d'un merge vert (`mark_outcome`). L'attribution est humaine : le terrain n'a aucun signal
automatique de revert/refix aval (cf. `db/merge_outcomes`). Un merge/feature inconnu remonte `KeyError` au
handler global (→ 404). AUCUN enregistrement de merge ici : le dénominateur naît au chokepoint du merge
(`gate/merge.py`), jamais d'une route."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cockpit.daemon.deps import Deps, get_deps
from cockpit.db import merge_outcomes


class MarkRequest(BaseModel):
    feature: str                                     # slug de la feature dont on marque l'issue aval
    outcome: Literal["held", "reverted", "refixed"]  # held = annuler une marque (422 hors-enum)
    note: str | None = None                          # raison libre (opt)
    sha: str | None = None                           # cibler un merge précis (défaut : le plus récent)


def make_reliability_router() -> APIRouter:
    router = APIRouter(prefix="/api/reliability", tags=["reliability"])

    @router.get("")
    def global_reliability(deps: Deps = Depends(get_deps)) -> dict:
        """Taux de fiabilité AGRÉGÉ (tous projets) + rollup par projet. Aucun merge vert → taux `None`
        (honnête-vide). Lecture idempotente."""
        conn = deps.open_db()
        try:
            return merge_outcomes.reliability(conn, None)
        finally:
            conn.close()

    @router.get("/{project}")
    def project_reliability(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Taux de fiabilité d'un projet + ligne par merge vert (outcome + pré-suggestion advisory). Projet
        absent → 404 (handler global)."""
        conn = deps.open_db()
        try:
            return merge_outcomes.reliability(conn, project)   # KeyError → 404
        finally:
            conn.close()

    @router.post("/{project}/mark")
    def mark(project: str, body: MarkRequest, deps: Deps = Depends(get_deps)) -> dict:
        """Marque l'issue aval d'un merge vert (revert/refix constaté après coup, ou retour à `held`). Merge
        introuvable → 404 (handler global). Numérateur du taux = ces marques confirmées."""
        conn = deps.open_db()
        try:
            return merge_outcomes.mark_outcome(conn, project=project, feature=body.feature,
                                               outcome=body.outcome, sha=body.sha, note=body.note)
        finally:
            conn.close()

    return router
