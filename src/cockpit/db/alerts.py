"""alerts — persistance des blocages de drain actionnables (table `alerts`, v17).

Doctrine « aucun blocage silencieux » : tout état qui empêche un drain d'avancer (gate rouge, hold socle,
worker failed, rate_limited, interrupted, interview requise) laisse une **ligne actionnable** — lue par le
daemon (`GET /api/alerts`) et poussée au centre d'alertes du header web. Émis au CHOKEPOINT de décision
(orchestrateur / merge), jamais dispersé dans `worker.py`/`reviewer.py`.

**Dédup par index unique PARTIEL** `ux_alerts_open(project, feature_ref, kind) WHERE status='open'` : un drain
ré-itère → le même motif recourt ; `emit_alert` fait un **UPSERT** (refresh `updated_at`, jamais
un doublon). Une ligne acked/resolved sort de l'index → une re-blocage après résolution ré-ouvre proprement.

**Lifecycle** `open|acked|resolved`. `resolve_alerts` ferme au succès (merge réussi / feature drainée / gate
redevenue verte) pour que le compteur du badge dise vrai.

**Best-effort, non négociable** (patron `gate/history.py`, `dispatch/reviewer._journal_non_run`) : une alerte
ratée ne masque ni ne casse le résultat métier → `try/except sqlite3.Error` + warning. Une alerte en échec ne
fait JAMAIS échouer un drain ni un merge. Exception : `ack_alert` est une MUTATION utilisateur (route API) →
lève `KeyError` sur un id inconnu (mappé 404), pas de silence.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from cockpit.core import ids

_LOG = logging.getLogger("cockpit")

# Colonnes exposées telles quelles (lecture API / test) — `findings` re-parsé en objet.
_COLS = ("id", "project", "feature_ref", "feature", "kind", "tier", "severity", "reason",
         "findings", "status", "created_at", "updated_at", "resolved_at")


def _now_iso() -> str:
    """Horodatage ISO-UTC — injectable en test (`created_at` est un argument, jamais rejoué à l'INSERT)."""
    return datetime.now(UTC).isoformat()


def emit_alert(conn: sqlite3.Connection, *, project: str, feature_ref: str, feature: str, kind: str,
               reason: str, tier: str | None = None, severity: str = "blocker",
               findings: Any = None, created_at: str | None = None) -> None:
    """Persiste (ou rafraîchit) une alerte OUVERTE pour `(project, feature_ref, kind)`. UPSERT sur l'index
    partiel `ux_alerts_open` → au plus une ligne ouverte par motif : une ré-détection au drain suivant met à
    jour `reason`/`tier`/`severity`/`findings`/`updated_at` sans créer de doublon (`created_at` = 1re
    détection, préservé). `findings` (dict/list) est sérialisé JSON. **Best-effort** : ne fait JAMAIS échouer
    l'appelant. `created_at` injecté (défaut = maintenant)."""
    ts = created_at or _now_iso()
    payload = json.dumps(findings, ensure_ascii=False) if findings is not None else None
    try:
        conn.execute(
            "INSERT INTO alerts (id, project, feature_ref, feature, kind, tier, severity, reason, "
            "findings, status, created_at, updated_at, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, NULL) "
            "ON CONFLICT (project, feature_ref, kind) WHERE status = 'open' DO UPDATE SET "
            "reason = excluded.reason, tier = excluded.tier, severity = excluded.severity, "
            "findings = excluded.findings, updated_at = excluded.updated_at",
            (ids.new_id(), project, feature_ref, feature, kind, tier, severity, reason, payload, ts, ts))
        conn.commit()
    except sqlite3.Error as exc:                            # best-effort : une alerte ratée ne casse rien
        _LOG.warning("alerte non persistée (%s, %s) : %s", feature_ref, kind, exc)


def resolve_alerts(conn: sqlite3.Connection, *, project: str, feature_ref: str,
                   kinds: tuple[str, ...] | None = None, resolved_at: str | None = None) -> None:
    """Ferme (open → resolved) les alertes ouvertes d'une feature — toutes, ou seulement `kinds`. Appelé aux
    ancres de SUCCÈS (merge réussi, feature drainée, gate redevenue verte) pour que le compteur dise vrai.
    **Best-effort**. `resolved_at` injecté (défaut = maintenant)."""
    ts = resolved_at or _now_iso()
    sql = "UPDATE alerts SET status = 'resolved', resolved_at = ? WHERE project = ? AND feature_ref = ? " \
          "AND status = 'open'"
    params: list[Any] = [ts, project, feature_ref]
    if kinds:
        sql += f" AND kind IN ({', '.join('?' * len(kinds))})"
        params.extend(kinds)
    try:
        conn.execute(sql, params)
        conn.commit()
    except sqlite3.Error as exc:                            # best-effort : résolution ratée ≠ succès raté
        _LOG.warning("alertes non résolues (%s/%s) : %s", project, feature_ref, exc)


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Ligne `alerts` → dict, `findings` re-parsé (None si absent/illisible)."""
    out = {c: row[c] for c in _COLS}
    raw = out.get("findings")
    if raw is not None:
        try:
            out["findings"] = json.loads(raw)
        except (ValueError, TypeError):
            out["findings"] = None
    return out


def list_alerts(conn: sqlite3.Connection, status: str = "open") -> list[dict]:
    """Alertes d'un statut donné (plus récent d'abord), `findings` re-parsé. Lecture PURE — sert la route
    `GET /api/alerts` (« qu'est-ce qui bloque, où, pourquoi ? » sans ouvrir la DB à la main)."""
    cols = ", ".join(_COLS)
    rows = conn.execute(
        f"SELECT {cols} FROM alerts WHERE status = ? ORDER BY created_at DESC, id DESC",
        (status,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def ack_alert(conn: sqlite3.Connection, alert_id: str) -> dict:
    """Marque une alerte OUVERTE comme acquittée (open → acked) et renvoie la ligne. Mutation utilisateur
    (route API) → lève `KeyError` si l'id est inconnu (mappé 404). Idempotent : une alerte déjà acked/resolved
    est renvoyée telle quelle sans erreur."""
    cols = ", ".join(_COLS)
    row = conn.execute(f"SELECT {cols} FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        raise KeyError(alert_id)
    if row["status"] == "open":
        conn.execute("UPDATE alerts SET status = 'acked' WHERE id = ? AND status = 'open'", (alert_id,))
        conn.commit()
        row = conn.execute(f"SELECT {cols} FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return _row_to_dict(row)
