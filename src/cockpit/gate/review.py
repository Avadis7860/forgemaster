"""review — verdict de review **Tier-1 lié au SHA** de la feature : le gate refuse de merger si le verdict
est absent, périmé (`reviewed_sha ≠ HEAD de la feature`) ou porte un 🔴 non-overridé.

Port de `services/loops/review_state.py`. Cœur PUR porté **verbatim** (garde déterministe `evidence ⊂ diff` :
un finding est rejeté à la source si sa citation verbatim n'apparaît pas dans une ligne AJOUTÉE du diff
`base...HEAD` — fail-closed anti-hallucination). Refactors :
- **#13** : l'état legacy `<root>/.claude/state/review-gate.json` en dur → sous `settings.home`, **clé par
  (projet, feature)** (`<home>/gate/<projet>/<feature>/review.json`). Aucun chemin projet codé en dur.
- Le SHA d'ancrage n'est plus « HEAD du repo courant » (le legacy tournait DANS le repo) mais **le SHA de la
  branche de la feature sur le SoT bare**, résolu par l'appelant (`git.feature_sha`) et injecté → `review`
  reste PUR de git (seul un I/O fichier pour l'état). `base` défaut = `dev` (main-suit-dev).

Tier-1 est **non-overridable au niveau du process** (une revue FRAÎCHE doit exister sur le HEAD mergé) ; un
🔴 reviewer est levable par un override humain explicite tracé (cf. `gate/merge.compose_merge_decision`).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cockpit.config import Settings

CONTRACT_VERSION = "review-gate-v2"
SEVERITIES = ("🔴", "🟡", "🟣")
DEFAULT_BASE = "dev"


def state_path(settings: Settings, project: str, feature: str) -> Path:
    """Chemin du verdict Tier-1, **clé par (projet, feature)** sous `settings.home` (#13, pas de chemin
    projet en dur). Un verdict par branche de feature = l'unité de merge."""
    return settings.home / "gate" / project / feature / "review.json"


def _counts(findings: list[dict]) -> dict[str, int]:
    out = {"red": 0, "yellow": 0, "purple": 0}
    key = {"🔴": "red", "🟡": "yellow", "🟣": "purple"}
    for f in findings:
        k = key.get(f.get("severity") or "")
        if k:
            out[k] += 1
    return out


# -- garde déterministe `evidence ⊂ diff` (portée verbatim de review_state.py) ----------------------

def _normalize(s: str) -> str:
    """Strip + collapse des espaces internes — comparaison robuste à l'indentation."""
    return " ".join(s.split())


def _added_texts_by_file(diff_text: str) -> dict[str, set[str]]:
    """Texte NORMALISÉ des lignes ajoutées (`+`) d'un diff unifié multi-fichiers, par fichier — le corpus
    citable. Une citation de finding doit s'y retrouver verbatim (le CONTENU, pas le numéro)."""
    out: dict[str, set[str]] = {}
    cur: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            cur = None if path == "/dev/null" else path
            continue
        if cur is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            txt = _normalize(line[1:])
            if txt:
                out.setdefault(cur, set()).add(txt)
    return out


def _extract_citation(evidence: str | None) -> str | None:
    """Portion CODE d'une evidence `"f.py:12 — <code>"` (après le séparateur «—»). None si l'evidence ne
    porte pas de citation verbatim (`file:line` seul) — ce qui vaudra rejet en aval."""
    if not evidence or "—" not in evidence:
        return None
    tail = evidence.split("—", 1)[1].strip()
    return tail or None


def _reject_reason(finding: dict, added: dict[str, set[str]]) -> str | None:
    """None si le finding est citable (`evidence ⊂ diff`) ; sinon la raison du rejet (fail-closed)."""
    file = finding.get("file")
    if not file or finding.get("line") in (None, ""):
        return "pas-de-file:line"
    citation = _extract_citation(finding.get("evidence"))
    if citation is None:
        return "pas-de-citation"
    if file not in added:
        return "file-absent-du-diff"
    needle = _normalize(citation)
    if any(needle in hay for hay in added[file]):
        return None
    return "citation-absente-du-diff"


def evidence_in_diff(finding: dict, diff_text: str) -> bool:
    """True ssi la citation verbatim du finding apparaît dans une ligne ajoutée du diff `base...HEAD`."""
    return _reject_reason(finding, _added_texts_by_file(diff_text)) is None


def partition_findings(findings: list[dict], diff_text: str) -> tuple[list[dict], list[dict]]:
    """Sépare `(kept, rejected)`. Fail-closed : un finding dont la citation n'apparaît pas dans le diff est
    REJETÉ ; chaque rejeté porte `reject_reason`. PUR (aucun git/réseau)."""
    added = _added_texts_by_file(diff_text)
    kept: list[dict] = []
    rejected: list[dict] = []
    for f in findings:
        reason = _reject_reason(f, added)
        if reason is None:
            kept.append(f)
        else:
            rejected.append({**f, "reject_reason": reason})
    return kept, rejected


def build_verdict(payload: dict, *, sha: str | None, ts: str, diff_text: str | None = None,
                  reviewer: str = "session") -> dict:
    """PUR (aucune I/O). Assemble le verdict `review-gate-v2` : applique la garde `evidence ⊂ diff`
    (`partition_findings`) si `diff_text` fourni (non citables → `rejected[]`, hors counts/gate), dérive les
    counts, fige `reviewed_sha`/`ts` **fournis par l'appelant** (jamais de fallback implicite git/horloge
    ici — c'est ce qui le rend pur). `payload` : {findings[], base?, nits_capped_at?}."""
    findings = payload.get("findings", [])
    rejected: list[dict] = []
    if diff_text is not None:
        findings, rejected = partition_findings(findings, diff_text)
    return {
        "contract_version": CONTRACT_VERSION,
        "reviewed_sha": sha,
        "base": payload.get("base", DEFAULT_BASE),
        "ts": ts,
        "reviewer": reviewer,
        "counts": _counts(findings),
        "nits_capped_at": payload.get("nits_capped_at"),
        "findings": findings,
        "rejected": rejected,
    }


def write_verdict(settings: Settings, project: str, feature: str, payload: dict, *,
                  sha: str | None, ts: str | None = None, diff_text: str | None = None) -> dict:
    """Persiste le verdict Tier-1 sous `state_path(settings, project, feature)`. `sha` injecté (le SHA de la
    branche de feature, résolu par l'appelant) ; `ts` défaut = horodatage UTC. Délègue la construction (pure)
    à `build_verdict`. `diff_text` fourni ⇒ garde `evidence ⊂ diff` appliquée."""
    verdict = build_verdict(
        payload, sha=sha,
        ts=ts or datetime.now(UTC).isoformat(timespec="seconds"), diff_text=diff_text)
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
    """True ssi un verdict existe ET porte le SHA courant de la feature (pas périmé). `current_sha` injecté
    (résolu par l'appelant via `git.feature_sha`) → PUR."""
    if not verdict:
        return False
    return bool(current_sha) and verdict.get("reviewed_sha") == current_sha


def gate_blocking(verdict: dict | None) -> bool:
    """True ssi le verdict porte au moins un 🔴 (bloque le merge sauf override humain explicite)."""
    return (verdict.get("counts", {}).get("red", 0) > 0) if verdict else False


def status(settings: Settings, project: str, feature: str, *, current_sha: str | None) -> dict:
    """Synthèse pour `gate/merge` : présent / frais / bloquant — même forme que `feature_verify.status`,
    consommée telle quelle par `compose_merge_decision`."""
    v = read_verdict(settings, project, feature)
    return {"present": v is not None, "fresh": is_fresh(v, current_sha=current_sha),
            "blocking": gate_blocking(v) if v else False,
            "counts": v.get("counts") if v else None,
            "reviewed_sha": v.get("reviewed_sha") if v else None}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit gate review <feature>` : lit le payload JSON `{findings[], base?}` sur **stdin**,
    résout le SHA + le diff `base...feature` sur le SoT, écrit le verdict Tier-1 SHA-bound. Fail-closed :
    diff introuvable/vide → PAS d'écriture d'un verdict non validé (exit ≠ 0)."""
    from cockpit.db import store
    from cockpit.git.internal import InternalGit
    from cockpit.projects.registry import get_project
    from cockpit.roadmap.model import resolve_feature

    conn = store.open_db(settings)
    try:
        project_slug, feature_slug = args.feature.split("/", 1) if "/" in args.feature else ("", "")
        feature = resolve_feature(conn, args.feature)     # KeyError/ValueError si absent
        sot = Path(get_project(conn, project_slug)["sot_path"])
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        conn.close()
        return 1
    finally:
        conn.close()
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError as exc:
        print(f"payload JSON invalide : {exc}", file=sys.stderr)
        return 2
    base = payload.get("base", DEFAULT_BASE)
    git = InternalGit()
    try:
        sha = git.feature_sha(sot, feature["branch"])
        diff_text = git.diff_text(sot, base=base, head=feature["branch"])
    except Exception as exc:  # noqa: BLE001 — SoT/branche KO : ne pas écrire un verdict non ancré
        print(f"diff {base}...{feature['branch']} introuvable : {exc}", file=sys.stderr)
        return 2
    if not diff_text.strip():
        print(f"diff {base}...{feature['branch']} vide — rien à valider, verdict non écrit", file=sys.stderr)
        return 2
    v = write_verdict(settings, project_slug, feature_slug, payload, sha=sha, diff_text=diff_text)
    nrej = len(v["rejected"])
    print(f"verdict écrit (sha {str(sha)[:8]}) — 🔴 {v['counts']['red']} · 🟡 {v['counts']['yellow']} · "
          f"🟣 {v['counts']['purple']}" + (f" — {nrej} finding(s) REJETÉ(s) non citable(s)" if nrej else ""))
    return 0
