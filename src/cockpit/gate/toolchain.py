"""toolchain — gate **Tier-0 NATIF** : lance la toolchain déterministe du projet dans le worktree de la
feature et prouve, **SHA-bound**, qu'elle passe. Peuple le slot `native_status` de `compose_merge_decision`
(veto déterministe **non-overridable**) — jusqu'ici jamais alimenté. Referme « vitest hors gate » (le front)
ET le trou **symétrique** backend (ruff/mypy/pytest), par le **même runner**.

Frontière (même patron que `gate/verify` et `gate/review`) :
- **verdict SHA-bound CACHÉ** sous `settings.home` ; `gate/merge.evaluate_gate` le **LIT** seulement. Le
  `GET /api/gate` est **poll-é** (invariant V4 : cheap/idempotent, le runner goto-only ne déclenche aucun
  effet) → on n'exécute JAMAIS la toolchain dans un GET. L'exécution est un **step séparé** (CLI
  `cockpit gate toolchain` / `POST …/toolchain`) qui **écrit** le verdict.
- **fail-CLOSED** : toolchain applicable (le diff la déclenche) mais verdict **absent/périmé/rouge** →
  bloque (déterministe, non-overridable). Non applicable → **N/A** (compose l'ignore, zéro régression).
- **auto-détection par CONVENTION** depuis le worktree (pas de config déclarative) : `web/package.json` avec
  un script `gate` → groupe **front** (`npm run gate` = eslint+vitest+build) ; `pyproject.toml` racine →
  groupe **backend** (`ruff check` → `mypy` → `pytest`). Steps ordonnés, arrêt au 1ᵉʳ rouge (`failed_step`).

Précondition : le worktree est **dep-ready** (le worker de dispatch a installé les deps). Deps front
absentes → un `npm ci` de secours est préfixé ; deps py absentes → le step échoue (rouge fail-closed).
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from cockpit.config import Settings
from cockpit.core.run import RunTimeout, run

CONTRACT_VERSION = "toolchain-gate-v1"
DEFAULT_TIMEOUT_S = 900          # npm ci + build (ou pytest) peut être long — borné pour ne pas pendre

# Déclencheurs (dérivés du DIFF seul, pour que `status`/`evaluate_gate` restent sans worktree) :
FRONT_DIR = "web/"               # un diff qui touche web/ → toolchain front applicable
PY_SUFFIX = ".py"                # un diff qui touche un *.py → toolchain backend applicable


# -- détection par convention (PUR) -----------------------------------------------------------------

def _has_front_gate(worktree: Path) -> bool:
    """True ssi le projet a une toolchain front conventionnelle : `web/package.json` avec un script `gate`."""
    pkg = worktree / "web" / "package.json"
    if not pkg.is_file():
        return False
    try:
        return "gate" in (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {})
    except (ValueError, OSError):
        return False


def detect_groups(worktree: Path) -> list[str]:
    """Groupes de toolchain présents dans le projet (convention) : `front` si `web/package.json` a un script
    `gate` ; `backend` si `pyproject.toml` à la racine. Sert le RUN (qui a le worktree)."""
    groups: list[str] = []
    if _has_front_gate(worktree):
        groups.append("front")
    if (worktree / "pyproject.toml").is_file():
        groups.append("backend")
    return groups


def touches_front(files: list[str]) -> bool:
    return any(FRONT_DIR in f for f in files)


def touches_py(files: list[str]) -> bool:
    return any(f.endswith(PY_SUFFIX) for f in files)


def applicable_triggers(diff_files: list[str]) -> list[str]:
    """Groupes DÉCLENCHÉS par le diff (dérivés du diff SEUL — pas besoin du worktree). Source d'autorité de
    l'applicabilité côté `status`/`evaluate_gate`."""
    trig: list[str] = []
    if touches_front(diff_files):
        trig.append("front")
    if touches_py(diff_files):
        trig.append("backend")
    return trig


def _steps_for(group: str, worktree: Path) -> list[dict]:
    """Steps ordonnés d'un groupe : `{name, argv, cwd}`. Front = [npm ci si node_modules absent] + npm run
    gate ; backend = ruff → mypy → pytest (cible `src` si src-layout, sinon `.`)."""
    if group == "front":
        web = worktree / "web"
        steps: list[dict] = []
        if not (web / "node_modules").is_dir():          # worktree pas dep-ready → secours (borné)
            steps.append({"name": "npm-ci", "argv": ["npm", "ci", "--prefer-offline", "--no-audit",
                                                      "--no-fund"], "cwd": web})
        steps.append({"name": "npm-run-gate", "argv": ["npm", "run", "gate"], "cwd": web})
        return steps
    # backend
    mypy_target = "src" if (worktree / "src").is_dir() else "."
    return [
        {"name": "ruff", "argv": ["ruff", "check", "."], "cwd": worktree},
        {"name": "mypy", "argv": ["mypy", mypy_target], "cwd": worktree},
        {"name": "pytest", "argv": ["pytest", "-q"], "cwd": worktree},
    ]


# -- runner (IMPUR : subprocess ; ne lève JAMAIS → step rouge fail-closed) ---------------------------

def run_toolchain(worktree: Path, diff_files: list[str], *, timeout_s: int = DEFAULT_TIMEOUT_S) -> list[dict]:
    """Lance les steps des groupes à la fois **présents** (détectés dans le worktree) ET **déclenchés** par le
    diff, dans l'ordre, en s'arrêtant au 1ᵉʳ rouge. Retourne la liste des résultats de step
    `{group, name, cmd, exit_code, ok, error?}`. Ne lève jamais (timeout/binaire absent → step rouge)."""
    present = set(detect_groups(worktree))
    groups = [g for g in applicable_triggers(diff_files) if g in present]
    results: list[dict] = []
    for group in groups:
        for step in _steps_for(group, worktree):
            argv = step["argv"]
            res: dict = {"group": group, "name": step["name"], "cmd": " ".join(argv)}
            try:
                r = run(argv, cwd=step["cwd"], timeout=timeout_s, check=False)
                res.update(exit_code=r.returncode, ok=r.ok)
                if not r.ok:
                    res["error"] = (r.stderr.strip() or r.stdout.strip())[:300]
            except (RunTimeout, OSError) as exc:             # timeout / binaire introuvable → rouge
                res.update(exit_code=None, ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
            results.append(res)
            if not res["ok"]:                                 # arrêt au 1ᵉʳ rouge
                return results
    return results


# -- verdict SHA-bound (Tier-0 natif) ---------------------------------------------------------------

def state_path(settings: Settings, project: str, feature: str) -> Path:
    """Verdict Tier-0-natif, clé par (projet, feature) sous `settings.home` (miroir de `gate/verify`)."""
    return settings.home / "gate" / project / feature / "toolchain.json"


def build_verdict(step_results: list[dict], *, sha: str | None, ts: str) -> dict:
    """PUR. Assemble le verdict. `ok=True` ssi tous les steps lancés sont verts (0 step = vacuously vert : le
    diff n'a déclenché aucune toolchain présente). `failed_step` = 1ᵉʳ step rouge. `sha`/`ts` injectés."""
    failed = next((s for s in step_results if not s.get("ok")), None)
    return {
        "contract_version": CONTRACT_VERSION,
        "reviewed_sha": sha,
        "ts": ts,
        "reviewer": "toolchain",
        "ok": all(s.get("ok") for s in step_results),
        "n_steps": len(step_results),
        "failed_step": failed["name"] if failed else None,
        "steps": step_results,
    }


def write_verdict(settings: Settings, project: str, feature: str, step_results: list[dict], *,
                  sha: str | None, ts: str | None = None) -> dict:
    """Persiste le verdict Tier-0-natif sous `state_path`. `sha` injecté (SHA de la branche de feature)."""
    verdict = build_verdict(step_results, sha=sha,
                            ts=ts or datetime.now(UTC).isoformat(timespec="seconds"))
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
    """True ssi un verdict existe ET porte le SHA courant de la feature. `current_sha` injecté → PUR."""
    if not verdict:
        return False
    return bool(current_sha) and verdict.get("reviewed_sha") == current_sha


def status(settings: Settings, project: str, feature: str, *, current_sha: str | None,
           diff_files: list[str]) -> dict:
    """Synthèse au format `native_status` consommé par `compose_merge_decision`. L'**applicabilité** dérive du
    DIFF seul (front↔`web/`, backend↔`*.py`) → pas besoin du worktree ici. Fail-CLOSED : applicable mais
    verdict absent/périmé → `ok=False` (bloque). Non applicable → `applicable=False` (N/A)."""
    trig = applicable_triggers(diff_files)
    if not trig:
        return {"applicable": False}
    summary = "/".join(trig)
    v = read_verdict(settings, project, feature)
    if not is_fresh(v, current_sha=current_sha):
        why = "toolchain non exécutée sur ce HEAD" if not v else "verdict périmé (reviewed_sha ≠ HEAD)"
        return {"applicable": True, "ok": False, "failed_step": why, "cmd": summary, "exit_code": ""}
    assert v is not None                                     # is_fresh garantit v présent
    if v.get("ok"):
        return {"applicable": True, "ok": True, "cmd": summary}
    return {"applicable": True, "ok": False, "failed_step": v.get("failed_step"), "cmd": summary,
            "exit_code": next((s.get("exit_code") for s in v.get("steps", []) if not s.get("ok")), None)}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit gate toolchain <feature>` : lance la toolchain (front/backend selon le diff) dans le
    worktree de la feature, écrit le verdict Tier-0-natif SHA-bound (ancré sur la branche de feature)."""
    from cockpit.db import store
    from cockpit.dispatch import worktree
    from cockpit.git.internal import GitOpError, InternalGit
    from cockpit.projects.registry import get_project
    from cockpit.roadmap.model import resolve_feature

    conn = store.open_db(settings)
    try:
        project_slug, feature_slug = args.feature.split("/", 1) if "/" in args.feature else ("", "")
        feature = resolve_feature(conn, args.feature)
        sot = Path(get_project(conn, project_slug)["sot_path"])
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    wt = worktree.worktree_path_for(settings, project_slug, feature_slug)
    if not wt.is_dir():
        print(f"🔴 worktree absent : {wt} — la feature doit être dispatchée (worktree vivant) avant le gate")
        return 1
    git = InternalGit()
    try:
        head_sha = git.feature_sha(sot, feature["branch"])
        diff_files = git.diff_names(sot, base="dev", head=feature["branch"])
    except GitOpError as exc:
        print(f"🔴 branche/diff introuvable : {exc}")
        return 1
    results = run_toolchain(wt, diff_files)
    verdict = write_verdict(settings, project_slug, feature_slug, results, sha=head_sha)
    for s in results:
        mark = "🟢" if s.get("ok") else "🔴"
        extra = f" (exit {s.get('exit_code')}: {s['error']})" if s.get("error") else ""
        print(f"  {mark} {s['group']}:{s['name']}{extra}")
    trig = applicable_triggers(diff_files)
    scope = "/".join(trig) if trig else "aucune toolchain déclenchée"
    print(f"toolchain [{scope}] : {'🟢 vert' if verdict['ok'] else '🔴 échec'} "
          f"({verdict['n_steps']} step(s), sha {str(head_sha)[:8]})")
    return 0 if verdict["ok"] else 1
