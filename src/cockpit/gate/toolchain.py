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
- **auto-détection par CONVENTION** depuis le worktree pour les **routes connues**, en **déléguant** la
  composition de la toolchain au script `gate` du projet (jamais de hardcode eslint/tsc/vitest) :
  `web/package.json` avec un script `gate` → groupe **front** ; un fichier node (`.ts/.js…`) **hors `web/`**
  (canonique `server/`, univers TS unifié) avec `server/package.json`+`gate` → groupe **backend-node** ;
  `pyproject.toml` racine → groupe **backend** (`ruff` → `mypy` → `pytest`). Un `package.json` **racine** avec
  un script `gate` couvre `web/` ET `server/` d'un seul run (workspaces). Steps ordonnés, arrêt au 1ᵉʳ rouge.
- **DÉCLARATION pour le résidu** (renversement 2026-07-31, cf. `docs/specs/tier0-native-toolchain-gate.md`
  §Amendement) : toute **source exécutée** qu'aucune route connue ne couvre (Go, Rust, shell, contrat de RUN…)
  déclenche le groupe **`declared`**, monté depuis la table `[bundle.gate]` du `.cockpit/bundle.toml` de la
  worktree. La charge de la preuve porte sur l'**absence de source**, jamais sur la reconnaissance du
  langage : le gate ne cherche plus à *reconnaître*, il exige d'être *renseigné*. **Zéro hardcode de stack.**
- **fail-CLOSED sur trigger non couvert** : un diff déclenche un groupe (route connue, ou résidu → `declared`)
  mais **aucune** unité de gate ne le prend en charge → **step rouge synthétique** (« toolchain non montable /
  non déclarée »), jamais un drop silencieux ni un vert à 0 step. Referme le faux-vert du dogfood void-runner
  (backend `server/` en TS mergé sans vérif, 2026-07-15) ET le trou d'applicabilité (langage inconnu mergé
  sans aucun étage déterministe, 2026-07-31). `N/A` est réservé aux diffs **sans source** (prose ⊕ verrous
  ⊕ assets binaires).

Précondition : le worktree est **dep-ready** (le worker de dispatch a installé les deps). Deps node absentes
→ un `npm ci` de secours est préfixé ; deps py absentes → le step échoue (rouge fail-closed).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import tomllib
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

# Suffixes de PROSE pure (docs). Un diff qui ne touche QUE ceux-là n'a aucune source à reviewer.
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt")

# NON-SOURCE **Tier-0** : ce qu'une TOOLCHAIN n'a rien à gater. Volontairement DISTINCT de `DOC_SUFFIXES`
# seul (qui sert au Tier-1) : une *review* veut voir un `.png` ou un `package-lock.json` bouger, une
# *toolchain* n'a rien à en dire. Non-source Tier-0 = prose ⊕ verrous de dépendances ⊕ assets binaires.
# Tout le reste est de la SOURCE (cadrage positif) — c'est ce qui rend l'applicabilité universelle.
_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".svg", ".bmp",
                   ".woff", ".woff2", ".ttf", ".otf", ".eot",
                   ".mp3", ".mp4", ".webm", ".wav", ".ogg", ".pdf", ".zip", ".gz", ".tar")
_LOCK_SUFFIXES = (".lock",)                              # poetry.lock, uv.lock, Cargo.lock, Gemfile.lock…
_LOCK_NAMES = ("package-lock.json", "npm-shrinkwrap.json", "go.sum")

# Messages fail-closed : trigger déclenché par le diff mais aucune unité de gate ne le couvre.
_ABSENT_MSG = {
    "front": "web/ modifié mais aucun package.json (racine ou web/) avec script `gate` — front non montable",
    "backend-node": "backend node modifié (hors web/, ex. server/) mais aucun package.json "
                    "(racine ou server/) avec script `gate` — backend-node non montable",
    "backend": "*.py modifié mais pas de pyproject.toml racine — toolchain backend python non montable",
    # `declared` : le message doit dire QUOI FAIRE, pas seulement ce qui manque — c'est le seul recours de
    # l'utilisateur dont la stack n'a aucune route connue (Go, Rust, shell, contrat de RUN…).
    "declared": "source non couverte par une route connue (ni web/, ni node hors web/, ni *.py) et aucune "
                "toolchain déclarée — ajoute une table [bundle.gate] dans .cockpit/bundle.toml, ex. :\n"
                '  [bundle.gate]\n'
                '  steps = [\n'
                '    { name = "vet",  argv = ["go", "vet", "./..."] },\n'
                '    { name = "test", argv = ["go", "test", "./..."] },\n'
                '  ]\n'
                "(`cwd` optionnel, relatif à la racine du worktree ; steps ordonnés, arrêt au 1ᵉʳ rouge)",
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
    """Groupes de toolchain PRÉSENTS (couvrables) dans le projet : `front` (web/ ou racine portant un script
    `gate`) ; `backend-node` (server/ ou racine portant un script `gate`) ; `backend` (`pyproject.toml`
    racine) — les trois par **convention** ; `declared` si le projet **déclare** sa toolchain
    (`[bundle.gate]` du `.cockpit/bundle.toml`). Descriptif — l'autorité du RUN est `_steps_for` (qui porte
    le fail-closed)."""
    groups: list[str] = []
    if _node_gate_dir(worktree, "front") is not None:
        groups.append("front")
    if _node_gate_dir(worktree, "backend-node") is not None:
        groups.append("backend-node")
    if (worktree / "pyproject.toml").is_file():
        groups.append("backend")
    if _declared_steps(worktree) is not None:
        groups.append("declared")
    return groups


def touches_front(files: list[str]) -> bool:
    return any(FRONT_DIR in f for f in files)


def touches_node_backend(files: list[str]) -> bool:
    """True ssi un fichier node (TS/JS) HORS `web/` est touché → backend node (canonique `server/`)."""
    return any(f.endswith(NODE_SUFFIXES) and FRONT_DIR not in f for f in files)


def touches_py(files: list[str]) -> bool:
    return any(f.endswith(PY_SUFFIX) for f in files)


def is_tier0_source(f: str) -> bool:
    """True ssi `f` est de la **source** au sens Tier-0 — c'est-à-dire tout ce qui n'est ni prose, ni verrou
    de dépendances, ni asset binaire. **Cadrage positif** : la charge de la preuve porte sur l'absence de
    source, jamais sur la reconnaissance du langage (un `.go`, un `.rs`, un `.sh`, un `Dockerfile` sont de la
    source parce qu'ils ne sont *rien de connu comme non-source*). PUR."""
    base = f.rsplit("/", 1)[-1]
    return not (f.endswith(DOC_SUFFIXES) or f.endswith(_ASSET_SUFFIXES)
                or f.endswith(_LOCK_SUFFIXES) or base in _LOCK_NAMES)


def _covered_by_known_route(f: str) -> bool:
    """True ssi `f` est déjà pris en charge par une **route connue** (front `web/` · node · python)."""
    return FRONT_DIR in f or f.endswith(NODE_SUFFIXES) or f.endswith(PY_SUFFIX)


def touches_undeclared_source(files: list[str]) -> bool:
    """True ssi le diff porte au moins une source **qu'aucune route connue ne couvre** (Go, Rust, shell,
    contrat de RUN `Dockerfile`/`compose.yaml`/`nginx.conf`, entrées de toolchain `pyproject.toml`/
    `tsconfig.json`…) → le groupe `declared` se déclenche et le projet doit avoir déclaré sa toolchain."""
    return any(is_tier0_source(f) and not _covered_by_known_route(f) for f in files)


def applicable_triggers(diff_files: list[str]) -> list[str]:
    """Groupes DÉCLENCHÉS par le diff (dérivés du diff SEUL — pas besoin du worktree ; invariant V4 : le
    `GET /api/gate` poll-é n'a que le diff sous la main). Source d'autorité de l'**applicabilité** côté
    `status`/`evaluate_gate` — la **montabilité**, elle, est l'autorité de `_steps_for` (qui reçoit le
    worktree). Ordre : front → backend-node → backend (python) → declared (le résidu).

    **Cadrage POSITIF** (renversement 2026-07-31) : les trois routes connues déclenchent leur groupe, et
    **tout résidu de source déclenche `declared`**. `[]` — donc Tier-0 **N/A** — est réservé aux diffs
    **sans source** : prose, verrous de dépendances, assets binaires (et diff vide). Un langage inconnu ne
    peut plus sortir en N/A : le seul veto non-overridable de la pile ne s'éteint plus en silence."""
    trig: list[str] = []
    if touches_front(diff_files):
        trig.append("front")
    if touches_node_backend(diff_files):
        trig.append("backend-node")
    if touches_py(diff_files):
        trig.append("backend")
    if touches_undeclared_source(diff_files):
        trig.append("declared")
    return trig


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


def _declared_steps(worktree: Path) -> list[dict] | None:
    """Steps **déclarés par le projet** dans `[bundle.gate].steps` du `.cockpit/bundle.toml` de la worktree,
    ou **None** si rien d'exploitable n'est déclaré. Lecteur calqué sur `provision/facet.resolve_facet_model`
    (tomllib, lecture locale, zéro réseau).

    **Déclaration malformée = déclaration absente** (fail-CLOSED, règle 3 de la spec) : manifeste
    absent/illisible, table absente, liste vide, step sans `argv` exploitable, `cwd` absolu ou non-str ⇒
    `None` → step rouge synthétique dans `run_toolchain`. Une déclaration cassée ne dégrade **jamais** vers
    le vert — sinon un `bundle.toml` mal tapé rouvrirait exactement le trou qu'on referme.

    **Aucun hardcode de langage** : on ne valide QUE la forme, jamais le contenu de l'`argv` (agnosticité par
    délégation — le projet déclare ce qu'il sait gater)."""
    manifest = worktree / ".cockpit" / "bundle.toml"
    if not manifest.is_file():
        return None
    try:
        gate = tomllib.loads(manifest.read_text(encoding="utf-8")).get("bundle", {}).get("gate")
    except (tomllib.TOMLDecodeError, OSError):
        return None
    if not isinstance(gate, dict):
        return None
    raw = gate.get("steps")
    if not isinstance(raw, list) or not raw:
        return None
    steps: list[dict] = []
    for i, decl in enumerate(raw):
        if not isinstance(decl, dict):
            return None
        argv = decl.get("argv")
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)):
            return None
        cwd_rel = decl.get("cwd")
        if cwd_rel is not None and not (isinstance(cwd_rel, str) and cwd_rel
                                        and not Path(cwd_rel).is_absolute()):
            return None
        name = decl.get("name")
        steps.append({"name": name if isinstance(name, str) and name else f"declared-{i + 1}",
                      "argv": list(argv), "cwd": worktree / cwd_rel if cwd_rel else worktree})
    return steps


def _steps_for(group: str, worktree: Path) -> list[dict] | None:
    """Steps ordonnés d'un groupe : `{name, argv, cwd}`, ou **None** si le groupe est DÉCLENCHÉ par le diff
    mais **non couvert** par une unité de gate présente (→ fail-closed dans `run_toolchain`). Node
    (`front`/`backend-node`) = [npm ci si node_modules absent] + `npm run gate` dans le dossier de l'unité
    (racine unifiée ou per-dir) ; `backend` (python) = ruff → mypy → pytest (cible `src` si src-layout,
    sinon `.`) ; `declared` = ce que le projet a **déclaré** (`[bundle.gate]`), verbatim.

    C'est ici — et pas dans `applicable_triggers` — que vit la **montabilité** : la fonction reçoit le
    worktree, l'applicabilité n'en a pas besoin. Cette séparation est ce qui garde `status`/`evaluate_gate`
    purs et le `GET /api/gate` cheap (invariant V4)."""
    if group == "declared":
        return _declared_steps(worktree)
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
    (`_steps_for` → None) produit un **step rouge synthétique** (« toolchain non montable / non déclarée »),
    jamais un drop silencieux ni un vert à 0 step. Un diff **sans source** (prose ⊕ verrous ⊕ assets) → `[]`
    (vacuously vert, légitime).

    **Dédup** : deux steps identiques (`name` + `cmd` + `cwd`) issus de groupes différents ne sont joués
    qu'**une fois** — un projet dont le `[bundle.gate]` déclare sa commande de gate conventionnelle ne la
    paie pas deux fois quand le diff déclenche aussi sa route connue."""
    results: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for group in applicable_triggers(diff_files):
        steps = _steps_for(group, worktree)
        if steps is None:                                     # déclenché mais non couvert → fail-closed
            results.append({"group": group, "name": "toolchain-absente", "cmd": "-",
                            "exit_code": None, "ok": False, "error": _ABSENT_MSG[group]})
            return results
        for step in steps:
            argv = step["argv"]
            key = (step["name"], " ".join(argv), str(step["cwd"]))
            if key in seen:                                   # déjà joué par un groupe précédent
                continue
            seen.add(key)
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
    `run_toolchain` ne rend `[]` que sur un diff **sans source** (prose ⊕ verrous ⊕ assets) — un trigger non
    couvert y produit un step rouge (fail-closed) → pas de faux-vert. `failed_step` = 1ᵉʳ rouge. `sha`/`ts`
    injectés."""
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
    DIFF seul (front↔`web/`, backend-node↔node hors `web/`, backend↔`*.py`, `declared`↔tout résidu de source)
    → pas besoin du worktree ici. Fail-CLOSED : applicable mais verdict absent/périmé → `ok=False` (bloque).
    Non applicable → N/A — réservé aux diffs **sans source** (prose ⊕ verrous ⊕ assets), plus jamais au cas
    « je ne reconnais pas ce langage »."""
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
