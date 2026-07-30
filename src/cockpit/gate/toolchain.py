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
- **auto-détection par CONVENTION** depuis le worktree (pas de config déclarative), en **déléguant** la
  composition de la toolchain au script `gate` du projet (jamais de hardcode eslint/tsc/vitest) :
  `web/package.json` avec un script `gate` → groupe **front** ; un fichier node (`.ts/.js…`) **hors `web/`**
  (canonique `server/`, univers TS unifié) avec `server/package.json`+`gate` → groupe **backend-node** ;
  `pyproject.toml` racine → groupe **backend** (`ruff` → `mypy` → `pytest`). Un `package.json` **racine** avec
  un script `gate` couvre `web/` ET `server/` d'un seul run (workspaces). Steps ordonnés, arrêt au 1ᵉʳ rouge.
- **fail-CLOSED sur trigger non couvert** : un diff déclenche un groupe (touche `web/`, un node hors `web/`,
  ou un `*.py`) mais **aucune** unité de gate ne le prend en charge → **step rouge synthétique** (« toolchain
  non montable »), jamais un drop silencieux ni un vert à 0 step. Referme le faux-vert du dogfood void-runner
  (backend `server/` en TS mergé sans vérif, 2026-07-15).

Précondition : le worktree est **dep-ready** (le worker de dispatch a installé les deps). Deps node absentes
→ un `npm ci` de secours est préfixé ; deps py absentes → le step échoue (rouge fail-closed).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from cockpit.config import Settings
from cockpit.core.run import RunTimeout, run
from cockpit.gate import history

CONTRACT_VERSION = "toolchain-gate-v1"
DEFAULT_TIMEOUT_S = 900          # npm ci + build (ou pytest) peut être long — borné pour ne pas pendre

# Déclencheurs (dérivés du DIFF seul, pour que `status`/`evaluate_gate` restent sans worktree) :
FRONT_DIR = "web/"               # un diff qui touche web/ → toolchain front applicable
PY_SUFFIX = ".py"                # un diff qui touche un *.py → toolchain backend (python) applicable
# un fichier node HORS web/ (canonique server/, univers TS unifié) → toolchain backend-node applicable :
NODE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")

# Messages fail-closed : trigger déclenché par le diff mais aucune unité de gate ne le couvre.
_ABSENT_MSG = {
    "front": "web/ modifié mais aucun package.json (racine ou web/) avec script `gate` — front non montable",
    "backend-node": "backend node modifié (hors web/, ex. server/) mais aucun package.json "
                    "(racine ou server/) avec script `gate` — backend-node non montable",
    "backend": "*.py modifié mais pas de pyproject.toml racine — toolchain backend python non montable",
}


# -- détection par convention (PUR) -----------------------------------------------------------------

def _has_gate_script(pkg: Path) -> bool:
    """True ssi `pkg` est un package.json portant un script npm `gate` (la toolchain node conventionnelle)."""
    if not pkg.is_file():
        return False
    try:
        return "gate" in (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {})
    except (ValueError, OSError):
        return False


def _node_gate_dir(worktree: Path, group: str) -> Path | None:
    """Dossier où lancer `npm run gate` pour un trigger node (`front`|`backend-node`), ou None si non couvert.
    Un `package.json` **racine** avec un script `gate` couvre tout (univers TS unifié / workspaces) ; sinon
    per-dir : `web/` pour `front`, `server/` pour `backend-node`."""
    if _has_gate_script(worktree / "package.json"):
        return worktree                                    # unité racine unifiée (couvre web/ ET server/)
    subdir = "web" if group == "front" else "server"
    d = worktree / subdir
    return d if _has_gate_script(d / "package.json") else None


def detect_groups(worktree: Path) -> list[str]:
    """Groupes de toolchain PRÉSENTS (couvrables) dans le projet, par convention : `front` (web/ ou racine
    portant un script `gate`) ; `backend-node` (server/ ou racine portant un script `gate`) ; `backend`
    (`pyproject.toml` racine). Descriptif — l'autorité du RUN est `_steps_for` (qui porte le fail-closed)."""
    groups: list[str] = []
    if _node_gate_dir(worktree, "front") is not None:
        groups.append("front")
    if _node_gate_dir(worktree, "backend-node") is not None:
        groups.append("backend-node")
    if (worktree / "pyproject.toml").is_file():
        groups.append("backend")
    return groups


def touches_front(files: list[str]) -> bool:
    return any(FRONT_DIR in f for f in files)


def touches_node_backend(files: list[str]) -> bool:
    """True ssi un fichier node (TS/JS) HORS `web/` est touché → backend node (canonique `server/`)."""
    return any(f.endswith(NODE_SUFFIXES) and FRONT_DIR not in f for f in files)


def touches_py(files: list[str]) -> bool:
    return any(f.endswith(PY_SUFFIX) for f in files)


def applicable_triggers(diff_files: list[str]) -> list[str]:
    """Groupes DÉCLENCHÉS par le diff (dérivés du diff SEUL — pas besoin du worktree). Source d'autorité de
    l'applicabilité côté `status`/`evaluate_gate`. Ordre : front → backend-node → backend (python)."""
    trig: list[str] = []
    if touches_front(diff_files):
        trig.append("front")
    if touches_node_backend(diff_files):
        trig.append("backend-node")
    if touches_py(diff_files):
        trig.append("backend")
    return trig


# Suffixes de PROSE pure (docs). Un diff qui ne touche QUE ceux-là n'a aucune source à reviewer.
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt")


def is_docs_only(files: list[str]) -> bool:
    """True ssi le diff ne touche QUE de la prose (suffixe doc) — aucune **source exécutable** → une review de
    CODE Tier-1 est N/A (comme Tier-0 natif l'est via `applicable_triggers`, Tier-1.5 via `verify.has_ui`).
    **Positif / fail-safe** : tout fichier non-prose (code, config, script, asset) rend `False` → review
    requise. Diff vide → `False` (rien à déclarer docs-only ; le gate reste fail-closed). PUR."""
    return bool(files) and all(f.endswith(DOC_SUFFIXES) for f in files)


def has_reviewable_code(files: list[str]) -> bool:
    """True ssi le diff porte du CODE à faire reviewer (Tier-1). False dans les DEUX cas sans code : diff
    **vide** (rien à reviewer — une feature au diff nul, ex. socle réconcilié dont le travail a landé
    ailleurs) ET diff **docs-only** (prose seule). Sépare « pas de code → review N/A » de `is_docs_only` (qui
    garde `empty→False` pour son propre fail-closed) : sans ce prédicat, un diff vide déclenchait une review
    que le reviewer skippe (« diff vide »), verdict jamais écrit → gate exige un verdict → **blocage
    circulaire** (feature immergeable, footgun constaté 2026-07-30). PUR."""
    return bool(files) and not is_docs_only(files)


def _steps_for(group: str, worktree: Path) -> list[dict] | None:
    """Steps ordonnés d'un groupe : `{name, argv, cwd}`, ou **None** si le groupe est DÉCLENCHÉ par le diff
    mais **non couvert** par une unité de gate présente (→ fail-closed dans `run_toolchain`). Node
    (`front`/`backend-node`) = [npm ci si node_modules absent] + `npm run gate` dans le dossier de l'unité
    (racine unifiée ou per-dir) ; `backend` (python) = ruff → mypy → pytest (cible `src` si src-layout,
    sinon `.`)."""
    if group in ("front", "backend-node"):
        d = _node_gate_dir(worktree, group)
        if d is None:                                    # trigger node non couvert → fail-closed
            return None
        steps: list[dict] = []
        if not (d / "node_modules").is_dir():            # worktree pas dep-ready → secours (borné)
            steps.append({"name": "npm-ci", "argv": ["npm", "ci", "--prefer-offline", "--no-audit",
                                                      "--no-fund"], "cwd": d})
        steps.append({"name": "npm-run-gate", "argv": ["npm", "run", "gate"], "cwd": d})
        return steps
    # backend (python)
    if not (worktree / "pyproject.toml").is_file():      # *.py touché mais pas de pyproject → fail-closed
        return None
    mypy_target = "src" if (worktree / "src").is_dir() else "."
    return [
        {"name": "ruff", "argv": ["ruff", "check", "."], "cwd": worktree},
        {"name": "mypy", "argv": ["mypy", mypy_target], "cwd": worktree},
        {"name": "pytest", "argv": ["pytest", "-q"], "cwd": worktree},
    ]


# -- runner (IMPUR : subprocess ; ne lève JAMAIS → step rouge fail-closed) ---------------------------

def run_toolchain(worktree: Path, diff_files: list[str], *, timeout_s: int = DEFAULT_TIMEOUT_S,
                  env: Mapping[str, str] | None = None) -> list[dict]:
    """Lance les steps des groupes à la fois **présents** (détectés dans le worktree) ET **déclenchés** par le
    diff, dans l'ordre, en s'arrêtant au 1ᵉʳ rouge. Retourne la liste des résultats de step
    `{group, name, cmd, exit_code, ok, error?}`. Ne lève jamais (timeout/binaire absent → step rouge).
    `env` (optionnel) REMPLACE l'environnement des steps — l'appelant compose depuis `os.environ` pour
    préfixer `tools/bin` au PATH (ruff/mypy/pytest/npm résolus sur un hôte frais) ; `None` = héritage
    passif (comportement historique, préservé pour les tests).

    **fail-CLOSED** : un groupe déclenché par le diff mais **non couvert** par une unité de gate présente
    (`_steps_for` → None) produit un **step rouge synthétique** (« toolchain non montable »), jamais un drop
    silencieux ni un vert à 0 step. Un diff sans trigger (doc-only) → `[]` (vacuously vert, légitime)."""
    results: list[dict] = []
    for group in applicable_triggers(diff_files):
        steps = _steps_for(group, worktree)
        if steps is None:                                     # déclenché mais non couvert → fail-closed
            results.append({"group": group, "name": "toolchain-absente", "cmd": "-",
                            "exit_code": None, "ok": False, "error": _ABSENT_MSG[group]})
            return results
        for step in steps:
            argv = step["argv"]
            res: dict = {"group": group, "name": step["name"], "cmd": " ".join(argv)}
            try:
                r = run(argv, cwd=step["cwd"], env=env, timeout=timeout_s, check=False)
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
    """PUR. Assemble le verdict. `ok=True` ssi tous les steps lancés sont verts. 0 step = vacuously vert, mais
    `run_toolchain` ne rend `[]` que sur un diff **sans trigger** (doc-only) — un trigger non couvert y
    produit un step rouge (fail-closed) → pas de faux-vert. `failed_step` = 1ᵉʳ rouge. `sha`/`ts` injectés."""
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
                  sha: str | None, ts: str | None = None, conn: sqlite3.Connection | None = None) -> dict:
    """Persiste le verdict Tier-0-natif sous `state_path`. `sha` injecté (SHA de la branche de feature).
    `conn` fourni ⇒ le verdict est aussi **archivé par SHA** dans `gate_verdicts` (best-effort) : le fichier
    reste le courant, la table l'historique."""
    verdict = build_verdict(step_results, sha=sha,
                            ts=ts or datetime.now(UTC).isoformat(timespec="seconds"))
    sp = state_path(settings, project, feature)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if conn is not None:
        history.record_verdict(conn, project, feature, "toolchain", verdict)
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
    DIFF seul (front↔`web/`, backend-node↔node hors `web/`, backend↔`*.py`) → pas besoin du worktree ici.
    Fail-CLOSED : applicable mais verdict absent/périmé → `ok=False` (bloque). Non applicable → N/A."""
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
    from cockpit.tools import tools_env
    results = run_toolchain(wt, diff_files, env=tools_env(settings))   # PATH: ruff/mypy/pytest/npm présents
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
