"""routes/alerts — router de domaine « alertes » (doctrine « aucun blocage silencieux », v17).

Lecture PURE des blocages persistés (`db/alerts.list_alerts`) pour le centre d'alertes POUSSÉ du header web
(poll court), et acquittement (`ack_alert`) — un id inconnu remonte `KeyError` au handler global (→ 404).
AUCUNE émission ici : les alertes naissent au chokepoint de décision (orchestrateur / merge), jamais d'une
route (une route ne « bloque » rien ; elle rend le blocage lisible)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from cockpit.daemon.deps import Deps, get_deps
from cockpit.db import alerts


def make_alerts_router() -> APIRouter:
    router = APIRouter(tags=["alerts"])

    @router.get("/api/alerts")
    def list_open_alerts(status: str = "open", deps: Deps = Depends(get_deps)) -> dict:
        """Alertes d'un statut donné (défaut `open`), plus récent d'abord, + leur compte — sert le
        badge-compteur et le centre déroulant du header (poll ~5 s). Lecture idempotente."""
        conn = deps.open_db()
        try:
            rows = alerts.list_alerts(conn, status=status)
        finally:
            conn.close()
        return {"alerts": rows, "count": len(rows)}

    @router.post("/api/alerts/{alert_id}/ack")
    def ack(alert_id: str, deps: Deps = Depends(get_deps)) -> dict:
        """Acquitte une alerte (open → acked) : elle sort du compteur du badge. `KeyError` si l'id est inconnu
        (→ 404 via le handler global). Idempotent : une alerte déjà acked/resolved est rendue telle quelle."""
        conn = deps.open_db()
        try:
            return alerts.ack_alert(conn, alert_id)
        finally:
            conn.close()

    return router
