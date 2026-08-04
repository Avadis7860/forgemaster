"""Tests de l'historique des verdicts de gate (`gate/history.py`, table `gate_verdicts` v11) : ce qui a été
rouge le reste (historisation PAR SHA), borne au merge, best-effort (jamais un verdict/merge raté)."""
from __future__ import annotations

import json
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.gate import history, review


def _db(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return settings, store.open_db(settings)


def test_verdict_history_preserves_red_across_green(tmp_path: Path):
    """`write_verdict(conn=…)` archive PAR SHA : un rouge à SHA-A survit à un vert à SHA-B (l'ancien
    `write_text` écrasait → T1 perdu). Le fichier disque reste le courant, la table est l'historique."""
    settings, conn = _db(tmp_path)
    red = {"findings": [{"severity": "🔴", "file": "a.py", "line": 1, "claim": "cassé",
                         "evidence": "a.py:1 — boom"}], "base": "dev"}
    review.write_verdict(settings, "proj", "feat", red, sha="sha-A", conn=conn)
    review.write_verdict(settings, "proj", "feat", {"findings": [], "base": "dev"}, sha="sha-B", conn=conn)
    rows = conn.execute("SELECT sha, gate, verdict FROM gate_verdicts WHERE feature='feat'").fetchall()
    assert {r["sha"] for r in rows} == {"sha-A", "sha-B"}   # les DEUX passages survivent
    assert all(r["gate"] == "review" for r in rows)
    va = json.loads(next(r["verdict"] for r in rows if r["sha"] == "sha-A"))
    assert va["counts"]["red"] >= 1                        # le rouge historisé RESTE rouge
    conn.close()


def test_verdict_history_is_best_effort(tmp_path: Path):
    """Best-effort : un puits d'historique cassé (table absente) ne fait JAMAIS échouer l'écriture du verdict
    (le fichier disque — résultat métier — est écrit ; l'INSERT raté est avalé, warning)."""
    settings, conn = _db(tmp_path)
    conn.execute("DROP TABLE gate_verdicts")
    conn.commit()
    v = review.write_verdict(settings, "proj", "feat", {"findings": [], "base": "dev"}, sha="s", conn=conn)
    assert v["reviewed_sha"] == "s"                        # verdict écrit malgré le puits cassé
    conn.close()


def test_prune_verdicts_keeps_last_n(tmp_path: Path):
    """La borne garde les `keep` derniers verdicts (created_at DESC) d'une feature — appelée AU MERGE, pas à
    la source (les orphelins au-delà de N sont ramassés)."""
    settings, conn = _db(tmp_path)
    keep = history.VERDICT_HISTORY_KEEP
    for i in range(keep + 5):
        history.record_verdict(conn, "proj", "feat", "review", {"reviewed_sha": f"s{i:03d}"},
                               created_at=f"2026-01-01T00:00:{i:02d}")
    history.prune_verdicts(conn, "proj", "feat")
    kept = {r[0] for r in conn.execute("SELECT sha FROM gate_verdicts WHERE feature='feat'")}
    assert len(kept) == keep
    assert f"s{keep + 4:03d}" in kept and "s000" not in kept   # gardés = les plus récents
    conn.close()
