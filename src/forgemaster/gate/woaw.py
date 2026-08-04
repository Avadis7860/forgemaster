"""woaw — verdict esthétique **advisory lié au SHA** de la feature : un juge (`site-vitrine-woaw-critic`)
note le RENDU d'une route (screenshot at-rest) contre les 7 principes woaw (P1–P7, doctrine
`docs/site-vitrine-woaw-language.md`) et rend des findings classés par sévérité. **Consultatif** : ce verdict
ne BLOQUE JAMAIS le merge — il est surfacé (reasons du gate + sortie CLI) pour orienter, jamais un veto.

Frontière avec `gate/review` (Tier-1) : le reviewer juge un **diff** (garde déterministe `evidence ⊂ diff`,
anti-hallucination) ; le juge woaw juge un **pixel rendu** — son evidence est **visuelle** (ce que l'écran
peint), pas citable dans le diff → **aucune garde diff ici** (elle rejetterait tout finding visuel légitime).
Le fail-closed du woaw n'est donc pas « citable » mais **advisory** : un verdict faible/absent n'invalide
rien, il informe. La promotion en bloquant-overridable (comme Tier-1) est un choix FUTUR, quand le flake du
juge est mesuré bas (cf. mission `cockpit-site-vitrine-capital-seed`, doctrine §4 « advisory d'abord »).

**v1 = verdict FICHIER-seul** (clé par (projet, feature) sous `settings.home`, convention `review`/`verify`
#13). La surface durable dans la cloche (`alerts`/`gate_verdicts`) exige un bump d'enum schéma (`kind`
`woaw_findings`, `gate` `woaw`, `tier` `woaw`) → **follow-up** (`cockpit-woaw-alert-surface`), volontairement
hors de l'axe advisory pour garder le blast-radius nul sur le contrat de schéma figé.

Le SHA d'ancrage = le SHA de la branche de feature (résolu par l'appelant, injecté → `woaw` reste PUR de git).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from forgemaster.config import Settings

CONTRACT_VERSION = "woaw-gate-v1"
SEVERITIES = ("🔴", "🟡", "🟣")


def state_path(settings: Settings, project: str, feature: str) -> Path:
    """Chemin du verdict woaw, clé par (projet, feature) sous `settings.home` (#13, pas de chemin en dur)."""
    return settings.home / "gate" / project / feature / "woaw.json"


def _counts(findings: list[dict]) -> dict[str, int]:
    out = {"red": 0, "yellow": 0, "purple": 0}
    key = {"🔴": "red", "🟡": "yellow", "🟣": "purple"}
    for f in findings:
        k = key.get(f.get("severity") or "")
        if k:
            out[k] += 1
    return out


def build_verdict(payload: dict, *, sha: str | None, ts: str, reviewer: str = "woaw-critic") -> dict:
    """PUR (aucune I/O). Assemble le verdict `woaw-gate-v1` à partir des findings du juge. **Pas de garde
    `evidence ⊂ diff`** : l'evidence woaw est visuelle (rendu), pas une ligne de diff. Dérive les counts par
    sévérité et capte le drapeau `flat` (le juge a-t-il atteint le seuil de plat §4.1 ?). `reviewed_sha`/`ts`
    fournis par l'appelant (jamais de fallback git/horloge → pur). `payload` : {findings[], route?, flat?}."""
    findings = payload.get("findings", [])
    return {
        "contract_version": CONTRACT_VERSION,
        "reviewed_sha": sha,
        "route": payload.get("route", "/"),
        "ts": ts,
        "reviewer": reviewer,
        "flat": bool(payload.get("flat", False)),
        "counts": _counts(findings),
        "findings": findings,
    }


def write_verdict(settings: Settings, project: str, feature: str, payload: dict, *,
                  sha: str | None, ts: str | None = None) -> dict:
    """Persiste le verdict woaw sous `state_path`. `sha` injecté (SHA de la branche de feature) ; `ts` défaut
    UTC. Délègue la construction (pure) à `build_verdict`. Fichier-seul (cf. module docstring : la surface
    cloche est un follow-up schéma)."""
    verdict = build_verdict(
        payload, sha=sha, ts=ts or datetime.now(UTC).isoformat(timespec="seconds"))
    sp = state_path(settings, project, feature)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verdict


def read_verdict(settings: Settings, project: str, feature: str) -> dict | None:
    sp = state_path(settings, project, feature)
    if not sp.is_file():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except ValueError:
        return None


def is_fresh(verdict: dict | None, *, current_sha: str | None) -> bool:
    """True ssi un verdict existe, porte le SHA courant de la feature ET le `CONTRACT_VERSION` courant.
    `current_sha` injecté (résolu par l'appelant via `git.feature_sha`) → PUR."""
    if not verdict:
        return False
    if verdict.get("contract_version") != CONTRACT_VERSION:
        return False
    return bool(current_sha) and verdict.get("reviewed_sha") == current_sha


def status(settings: Settings, project: str, feature: str, *, current_sha: str | None) -> dict:
    """Synthèse pour `gate/merge` — **advisory** : jamais de clé `blocking` (le woaw ne bloque pas). Surface
    `present`/`fresh`/`counts`/`flat`/`route` → `compose_merge_decision` la rend en reason consultative."""
    v = read_verdict(settings, project, feature)
    return {"present": v is not None, "fresh": is_fresh(v, current_sha=current_sha),
            "counts": v.get("counts") if v else None, "flat": v.get("flat") if v else None,
            "route": v.get("route") if v else None,
            "reviewed_sha": v.get("reviewed_sha") if v else None}
