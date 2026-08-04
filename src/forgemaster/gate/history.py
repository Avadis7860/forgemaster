"""history — historique DB des verdicts de gate (table `gate_verdicts`, v11).

`write_verdict` garde le fichier `gate/<p>/<f>/*.json` **courant** (la fraîcheur se lit en un `read_text`) ;
ici on **archive** chaque passage PAR SHA pour que « ce qui a été rouge le reste » : un verdict rouge à
SHA-A **survit** à un verdict vert à SHA-B (l'ancien `write_text` écrasait → T1 perdu). Contrat de lecture
**assumé** : la table est l'historique, le fichier est le courant — l'inverse du « table-d'abord / disque
-fallback » de `dispatch-history-persistence`, parce qu'ici le disque **n'est pas volatil** (forgemaster
local).

**Best-effort, non négociable** (`corpus/decision/projects/2026-06-20--dispatch-jobs-journal.md`) : une trace
ratée ne masque ni ne casse le résultat métier → `try/except` + warning. Un historique en échec ne fait JAMAIS
échouer un merge autorisé ni un verdict écrit.

**PAS de persistance de la `decision`** : `compose_merge_decision` est PURE (`merge.py`) → une décision
passée se **rejoue** depuis ses verdicts historisés. Historiser les verdicts suffit ; persister la décision
dupliquerait une sortie déterministe (cf. la pureté préservée, hors scope de cette task).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime

from forgemaster.core import ids

# Verdicts gardés PAR (projet, feature) — borné AU MERGE, pas à la source (« tail-N au merge » ,
# `2026-06-16--aggregator-signal-history-bounding`). Généreux : la donnée est minuscule (mesure S6/S7).
VERDICT_HISTORY_KEEP = 20


def _now_iso() -> str:
    """Horodatage ISO-UTC — injectable en test (le `created_at` est un argument, pas rejoué à l'INSERT)."""
    return datetime.now(UTC).isoformat()


def record_verdict(conn: sqlite3.Connection, project: str, feature: str, gate: str, verdict: dict, *,
                   created_at: str | None = None) -> None:
    """Archive un verdict SHA-bound dans `gate_verdicts` (`gate ∈ review|toolchain|merge`). Le SHA vient de
    `verdict['reviewed_sha']` (déjà ancré par `build_verdict`). **Best-effort** : ne fait JAMAIS échouer
    l'appelant (un merge/verdict qui a réussi le reste). `created_at` injecté (défaut = maintenant)."""
    try:
        conn.execute("INSERT INTO gate_verdicts (id, project, feature, gate, sha, verdict, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (ids.new_id(), project, feature, gate, (verdict or {}).get("reviewed_sha") or "",
                      json.dumps(verdict, ensure_ascii=False), created_at or _now_iso()))
        conn.commit()
    except sqlite3.Error as exc:                            # best-effort : une trace ratée ne casse rien
        logging.getLogger("forgemaster").warning("verdict non historisé (%s/%s) : %s", project, feature, exc)


def list_verdicts(conn: sqlite3.Connection, project: str, feature: str, *,
                  limit: int = VERDICT_HISTORY_KEEP) -> list[dict]:
    """Historique des verdicts d'une feature (plus récent d'abord), `verdict` JSON re-parsé. Lecture PURE —
    sert la route d'historique (« qu'est-ce qui a échoué et quand ? » sans ouvrir la DB à la main)."""
    rows = conn.execute(
        "SELECT gate, sha, verdict, created_at FROM gate_verdicts WHERE project = ? AND feature = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?", (project, feature, limit)).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            verdict = json.loads(r["verdict"])
        except (ValueError, TypeError):
            verdict = None
        out.append({"gate": r["gate"], "sha": r["sha"], "verdict": verdict, "created_at": r["created_at"]})
    return out


def prune_verdicts(conn: sqlite3.Connection, project: str, feature: str, *,
                   keep: int = VERDICT_HISTORY_KEEP) -> None:
    """Borne l'historique d'une feature à ses `keep` derniers verdicts (appelé AU MERGE, pas à la source) —
    même point de sortie que `delete_branch`, qui sinon orpheline les verdicts. **Best-effort**."""
    try:
        conn.execute(
            "DELETE FROM gate_verdicts WHERE project = ? AND feature = ? AND id NOT IN "
            "(SELECT id FROM gate_verdicts WHERE project = ? AND feature = ? "
            " ORDER BY created_at DESC, id DESC LIMIT ?)",
            (project, feature, project, feature, keep))
        conn.commit()
    except sqlite3.Error as exc:                            # best-effort : borne ratée ≠ merge raté
        logging.getLogger("forgemaster").warning("historique non borné (%s/%s) : %s", project, feature, exc)
