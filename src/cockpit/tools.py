"""tools — provisionnement **hôte-niveau** de l'outillage que les bundles DÉCLARENT (les 3 cartes + Node +
qualité py), dans un venv d'outils dédié sous `$COCKPIT_HOME/tools/`, exposé sur un **unique** `tools/bin`
que le dispatch worker ET le gate natif préfixent au PATH. Ferme le fossé « déclaré → présent » (P0 de
l'épic tooling-fulfillment) : `bundles/base/CLAUDE.md` promet `codemap`/`docsmap`/`frontmap`/`node`/`ruff`…
mais le wheel n'expose que `cockpit` (`codemap` n'est qu'un module `-m codemap`), les cartes voisines ne sont
que *clonées* (jamais pip-installées), Node n'est provisionné nulle part, et le worker spawn en `env=None`
(PATH systemd minimal, hérité passif) — même présents, il ne les verrait pas.

`taskmap` n'est PAS provisionné ici : ce n'est pas une carte de contenu par-projet (comme codemap/docsmap/
frontmap) mais le **moteur d'ordonnancement central** (`taskmap.core`), importé en-process par
`roadmap/resolver.py`. La lib est fournie autrement (vendorée au wheel par `deploy/build-wheel.sh` ; editable
en dev via `webbuild.ensure_maps`) ; aucun projet n'a de task-graph local à mapper → pas de CLI host exposé.

Conception (mêmes conventions que `dispatch`/`codemap`) : des seams **PURS** testables sans subprocess —
`tools_bin`/`tools_env` (composition PATH), `install_plan` (quels paquets, quel venv, quels symlinks : les
argv pip/nodeenv) ; l'exécution passe par un `runner` **injecté**. Le pip/nodeenv réel est prouvé à la
vérif install fraîche, jamais au gate déterministe.

Pas dans le venv du cockpit (ne pas polluer ses deps ni son PATH systemd) : un venv **séparé** sous
COCKPIT_HOME. Node via **nodeenv** (rootless, pip-natif) → autonome, marche en portée `--user` sans sudo.
Les 3 cartes sont **publiques** (2026-08-03) : le clone est **ANONYME**, et aucun credential n'entre ici —
ni `token`, ni `token_ref`, ni `credential_env`. Le chemin d'auth a été retiré plutôt que laissé dormant :
tant qu'il existait, chaque E2E tournait sous une configuration qu'aucun utilisateur n'aura jamais, donc
prouvait une fiction. `GIT_TERMINAL_PROMPT=0` **reste** — sans lui, un repo devenu injoignable (renommé,
re-privatisé) ferait *pendre* pip sur un prompt de credentials jusqu'au timeout, au lieu d'échouer net.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from cockpit.config import Settings
from cockpit.core.run import RunResult, run

Runner = Callable[..., RunResult]


class ToolPreflightError(RuntimeError):
    """Un binaire déclaré par la facette active (`allowedTools`) ne résout pas sur le PATH du worker.
    Levé AVANT le spawn (fail-loud, actionnable) : le worker ne découvre plus l'absence à l'usage."""

# Les 3 cartes de contenu par-projet, packagées (console_scripts codemap/docsmap/frontmap). Installées depuis
# leur repo GitHub PUBLIC à une réf suivie, en clone ANONYME — aucun credential sur ce chemin.
# (task-map exclu : moteur central importé en-process, pas une carte host — cf. docstring du module.)
MAP_REPOS: dict[str, str] = {
    "code-map": "https://github.com/Avadis7860/code-map.git",
    "docs-map": "https://github.com/Avadis7860/docs-map.git",
    "front-map": "https://github.com/Avadis7860/front-map.git",
}
MAP_REF = "main"
# Outils qualité Python (extra `cockpit[dev]`, NON tirés par `pip install <wheel>` — deps runtime seules).
PY_QUALITY: tuple[str, ...] = ("ruff", "pytest", "mypy")
# Node LTS via nodeenv (rootless) — prefix autonome sous tools/nodeenv, ses bin symlinkés dans tools/bin.
NODE_VERSION = "lts"
# Exécutables exposés sur tools/bin — ceux que le worker et le gate résolvent (par-type : le worker n'utilise
# que ceux de sa facette, mais on expose tout une fois ; le preflight P1 vérifie la présence par-facette).
_VENV_BINS: tuple[str, ...] = ("codemap", "docsmap", "frontmap", "ruff", "pytest", "mypy")
_NODE_BINS: tuple[str, ...] = ("node", "npm", "npx")
# Catalogue des outils que l'HÔTE provisionne (`cockpit tools install` → `tools/bin`) : ceux-là DOIVENT
# préexister au dispatch → le preflight les gate. Les autres binaires qu'une facette déclare (`eslint`,
# `tsc`, `vitest`, `pip install`…) sont **projet-locaux** (le worker les installe via `npm install`/le venv
# projet) → jamais dans `tools/bin`, jamais gatés (sinon on bloquerait un worktree neuf à tort).
HOST_TOOLS: frozenset[str] = frozenset((*_VENV_BINS, *_NODE_BINS))

_STEP_TIMEOUT_S = 900   # pip (clone git + build) / nodeenv (download Node) : lents mais bornés (fail-loud).


# -- seams PURS (composition de chemins / PATH — zéro subprocess) ------------------------------------

def tools_root(settings: Settings) -> Path:
    """Racine de l'outillage hôte-niveau : `$COCKPIT_HOME/tools/`."""
    return settings.home / "tools"


def tools_venv(settings: Settings) -> Path:
    """Venv Python DÉDIÉ des outils (séparé du venv cockpit) : `tools/venv`."""
    return tools_root(settings) / "venv"


def nodeenv_prefix(settings: Settings) -> Path:
    """Prefix Node autonome installé par nodeenv : `tools/nodeenv` (contient `bin/node`, `bin/npm`)."""
    return tools_root(settings) / "nodeenv"


def tools_bin(settings: Settings) -> Path:
    """Le RÉPERTOIRE bin unique où tous les exécutables sont symlinkés — une seule entrée PATH."""
    return tools_root(settings) / "bin"


def tools_env(settings: Settings, *, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Compose un env dont le PATH est préfixé par `tools/bin` PUIS `$HOME/.local/bin` — passé au subprocess
    worker et au gate natif (dont `core.run` **remplace** l'env, l'appelant compose donc depuis `os.environ`).
    `~/.local/bin` porte `claude` (le moteur du worker, posé par `--with-claude`) : le PATH systemd minimal du
    daemon ne source pas `~/.profile`, sans cet ajout un dispatch daemon-triggered ne résout pas `claude`
    (worker mort-né). Ne mute pas `base`/`os.environ`. PUR."""
    env = dict(base if base is not None else os.environ)
    tb = str(tools_bin(settings))
    local_bin = str(Path(env.get("HOME", str(Path.home()))) / ".local" / "bin")
    path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([tb, local_bin, *([path] if path else [])])
    return env


def cli_env(settings: Settings, *, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """`tools_env` PRÉFIXÉ du bin du venv COURANT (là où vit le script `cockpit`). Pour les surfaces où un
    humain / `claude` invoque `cockpit …` DIRECTEMENT — l'interview (`interview.interview_env`) et le
    **terminal web** (`pty.shell_env`) : `cockpit` n'est PAS dans `tools/bin` (le worker et le gate n'en ont
    pas besoin, eux). Sans ce préfixe, la surface hérite d'un PATH (shell de login / systemd minimal) sans
    `cockpit` → `cockpit interview`/`cockpit roadmap …` en `command not found`. PUR."""
    env = tools_env(settings, base=base)
    cockpit_bin = str(Path(sys.executable).parent)          # /…/venv/bin — porte le script `cockpit`
    env["PATH"] = os.pathsep.join([cockpit_bin, env["PATH"]])
    return env


# -- preflight de présence (P1 : ce que la facette DÉCLARE doit résoudre) -----------------------------

def required_bins(settings_local: Path) -> set[str]:
    """Binaires exigés par une facette = les entrées `Bash(<cmd>:*)` de `permissions.allow` d'un
    `settings.local.json`, réduites à leur exécutable (1er mot du préfixe : `Bash(pip install:*)` → `pip`,
    `Bash(codemap:*)` → `codemap`). Ignore les entrées non-`Bash(...)` (`Read`, `Glob`, `Edit`…). Fail-soft :
    fichier absent/illisible/mal formé → `set()` (rien à exiger, on ne bloque pas un dispatch sain). PUR."""
    try:
        data = json.loads(Path(settings_local).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    allow = (data.get("permissions") or {}).get("allow") or []
    bins: set[str] = set()
    for entry in allow:
        if not (isinstance(entry, str) and entry.startswith("Bash(") and entry.endswith(")")):
            continue
        inner = entry[len("Bash("):-1]           # `pip install:*`, `ruff:*`, `node:*`
        prefix = inner.split(":", 1)[0]           # retire le glob `:*` → `pip install`, `ruff`
        head = prefix.split()                     # 1er mot = l'exécutable → `pip`, `ruff`
        if head:
            bins.add(head[0])
    return bins


def missing_bins(bins: set[str], env: Mapping[str, str]) -> list[str]:
    """Sous-ensemble de `bins` qui ne résout PAS via `env["PATH"]` (`shutil.which`), trié. PUR."""
    path = env.get("PATH", "")
    return sorted(b for b in bins if shutil.which(b, path=path) is None)


def preflight_tools(worktree: Path, settings: Settings, *, env: Mapping[str, str] | None = None) -> None:
    """Vérifie que tout binaire déclaré par la facette active (`<worktree>/.claude/settings.local.json`,
    posé par `activate_facet`) résout sur le PATH du worker (`tools_env`). Absent → `ToolPreflightError`
    fail-loud AVANT le spawn. No-op si la facette n'exige aucun binaire (fail-soft de `required_bins`)."""
    env = env if env is not None else tools_env(settings)
    declared = required_bins(Path(worktree) / ".claude" / "settings.local.json")
    missing = missing_bins(declared & HOST_TOOLS, env)   # ne gate QUE les outils hôte-provisionnés
    if missing:
        raise ToolPreflightError(
            f"outils déclarés par la facette absents du PATH worker : {', '.join(missing)} — "
            f"pose-les (`cockpit tools install`) puis relance. worktree={worktree}")


def _symlink_sources(settings: Settings) -> dict[str, Path]:
    """Table `nom d'exécutable → source réelle` à exposer dans `tools/bin`. PUR (chemins seulement)."""
    venv_bin = tools_venv(settings) / "bin"
    node_bin = nodeenv_prefix(settings) / "bin"
    srcs: dict[str, Path] = {name: venv_bin / name for name in _VENV_BINS}
    srcs.update({name: node_bin / name for name in _NODE_BINS})
    return srcs


def install_plan(settings: Settings) -> list[dict[str, object]]:
    """Étapes ordonnées `{name, argv}` de l'install (PUR — construit les argv, n'exécute rien). Un seul
    `pip install` pour les 3 cartes (`git+<url>@<ref>`) + les outils qualité py ; puis nodeenv ; puis Node."""
    pip = str(tools_venv(settings) / "bin" / "pip")
    map_specs = [f"git+{url}@{MAP_REF}" for url in MAP_REPOS.values()]
    return [
        {"name": "pip-tools", "argv": [pip, "install", "--upgrade", *map_specs, *PY_QUALITY]},
        {"name": "pip-nodeenv", "argv": [pip, "install", "--upgrade", "nodeenv"]},
        {"name": "nodeenv", "argv": [str(tools_venv(settings) / "bin" / "nodeenv"),
                                     f"--node={NODE_VERSION}", "--force", str(nodeenv_prefix(settings))]},
    ]


# -- exécution (IMPUR : subprocess via runner injecté ; symlinks) ------------------------------------

def _default_runner(argv: list[str], *, env: Mapping[str, str] | None, timeout: float) -> RunResult:
    return run(argv, env=env, timeout=timeout, check=False)


def anonymous_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """L'env des clones git de pip : l'ambiant, **sans aucun credential**, et `GIT_TERMINAL_PROMPT=0`.

    Les 3 cartes sont publiques → le clone est anonyme. Ce seam existe pour que ça reste vrai : il ne
    compose aucun `url.…insteadOf`, donc aucun token ne peut se glisser dans l'env d'un enfant git (le test
    l'asserte). Le `GIT_TERMINAL_PROMPT=0` n'est pas décoratif — un repo renommé ou re-privatisé fait
    *pendre* git sur un prompt jusqu'au timeout de 900 s, au lieu de rendre une erreur lisible."""
    env = dict(base if base is not None else os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def install_tools(settings: Settings, *, runner: Runner | None = None) -> dict:
    """Provisionne l'outillage hôte-niveau (IDEMPOTENT, FAIL-LOUD). Crée le venv d'outils, installe les 3
    cartes + qualité py + Node (nodeenv), puis symlinke chaque exécutable dans `tools/bin`. Les cartes sont
    publiques : clone **anonyme**, aucun credential n'entre par ici. Une étape rouge (rc≠0) **abandonne**
    (jamais un demi-provisioning) et retourne `{ok:False, steps, error}`. Retour
    `{ok, steps:[{name, ok, exit_code, error?}], symlinks:[nom]}`.
    """
    runner = runner or _default_runner
    root = tools_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"ok": True, "steps": [], "symlinks": []}
    steps: list[dict] = report["steps"]  # type: ignore[assignment]

    # 1. venv d'outils (idempotent : `python -m venv` sur un venv existant est sûr).
    venv_step = {"name": "venv", "argv": [sys.executable, "-m", "venv", str(tools_venv(settings))]}
    if not _run_step(runner, venv_step, env=dict(os.environ), steps=steps):
        report["ok"] = False
        report["error"] = "création du venv d'outils échouée"
        return report

    # 2. env des clones git de pip : anonyme, aucun credential (les 3 cartes sont publiques).
    env = anonymous_env()

    # 3. installs (fail-loud : abandon au 1er rouge).
    for step in install_plan(settings):
        if not _run_step(runner, step, env=env, steps=steps):
            report["ok"] = False
            report["error"] = f"étape {step['name']} échouée"
            return report

    # 4. symlinks vers le bin unique (idempotent : on remplace un lien existant). Une source manquante après
    #    des installs vertes = incohérence → fail-loud.
    bin_dir = tools_bin(settings)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, src in _symlink_sources(settings).items():
        if not src.exists():
            report["ok"] = False
            report["error"] = f"exécutable attendu absent après install : {src}"
            return report
        link = bin_dir / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(src)
        report["symlinks"].append(name)  # type: ignore[attr-defined]
    return report


def _run_step(runner: Runner, step: dict, *, env: Mapping[str, str], steps: list[dict]) -> bool:
    """Lance une étape via le runner, journalise le résultat dans `steps`, retourne `ok`. Ne lève pas :
    une erreur de transport (binaire absent…) devient un step rouge."""
    from cockpit.core.run import RunError, RunTimeout
    entry: dict = {"name": step["name"]}
    try:
        r = runner(step["argv"], env=env, timeout=_STEP_TIMEOUT_S)
        entry.update(ok=r.ok, exit_code=r.returncode)
        if not r.ok:
            entry["error"] = (r.stderr.strip() or r.stdout.strip())[:300]
    except (RunTimeout, RunError, OSError) as exc:
        entry.update(ok=False, exit_code=None, error=f"{type(exc).__name__}: {exc}"[:300])
    steps.append(entry)
    return bool(entry["ok"])


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit tools install` : provisionne l'outillage hôte-niveau. Aucun credential — les 3 cartes
    sont publiques, le clone est anonyme (le `--token-file` d'avant a été RETIRÉ, pas rendu optionnel : un
    drapeau accepté-et-ignoré ferait croire qu'il sert encore). Imprime chaque étape ; code de sortie 1 si
    une étape a échoué (fail-loud)."""
    report = install_tools(settings)
    for s in report["steps"]:  # type: ignore[attr-defined]
        mark = "🟢" if s.get("ok") else "🔴"
        extra = f" (exit {s.get('exit_code')}: {s['error']})" if s.get("error") else ""
        print(f"  {mark} {s['name']}{extra}")
    if report["ok"]:
        n = len(report["symlinks"])  # type: ignore[arg-type]
        print(f"outillage provisionné → {tools_bin(settings)} ({n} exécutable(s) exposé(s)).")
        return 0
    print(f"🔴 échec : {report.get('error')}")
    return 1
