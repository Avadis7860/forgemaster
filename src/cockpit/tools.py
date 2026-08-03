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


# -- provenance de l'outillage SERVI (lecture LOCALE, zéro réseau, ne lève jamais) --------------------
#
# Une instance tire les 3 cartes UNE FOIS, au provisioning, à `MAP_REF` — une réf MOBILE. Rien ne
# re-synchronise ensuite, et `preflight_tools` ne teste qu'une PRÉSENCE : l'instance sert donc des cartes
# qui vieillissent sans que rien ne le dise. Mesuré le 2026-08-03 : un écart né en moins de 4 h.
#
# Il n'y a AUCUN tampon à écrire pour le savoir — pip pose déjà `direct_url.json` (PEP 610) dans le
# `dist-info` à l'install, avec le `commit_id` RÉSOLU. On LIT ce qui existe. Même mécanisme que la
# provenance de `mcp-catalogs` (un mécanisme, deux consommateurs) et même contrat de dégradation que
# `build_provenance.read_stamp` : un `sha=None` s'accompagne TOUJOURS d'un `reason`, jamais d'un silence.
_SHA_LENGTHS = (40, 64)             # sha1 (défaut de git) et sha256 (transition amont)


def _looks_like_sha(value: str) -> bool:
    """Une chaîne a-t-elle la FORME d'un SHA git. PUR. Garde-fou : mieux vaut avouer « pas un SHA
    reconnaissable » que servir une valeur arbitraire lue dans un JSON comme si c'était une identité."""
    return len(value) in _SHA_LENGTHS and all(c in "0123456789abcdef" for c in value.lower())


def _read_text(path: Path) -> str | None:
    """Le contenu d'un fichier, ou None s'il est absent/illisible. PUR-ish (lecture seule), ne lève pas."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def site_packages(settings: Settings) -> Path | None:
    """Le `site-packages` du venv d'outils (`tools/venv/lib/python*/site-packages`), ou None s'il n'existe
    pas — cas NORMAL d'un cockpit en checkout dev où `tools install` n'a jamais tourné. Ne lève pas."""
    try:
        for p in sorted((tools_venv(settings) / "lib").glob("python*/site-packages")):
            if p.is_dir():
                return p
    except OSError:
        return None
    return None


def _dist_info(sp: Path, dist_name: str) -> Path | None:
    """Le `.dist-info` d'une distribution dans ce site-packages. Le nom de dossier est NORMALISÉ (PEP 503 :
    `code-map` → `code_map`), d'où le glob plutôt qu'un chemin construit. Ne lève pas."""
    stem = dist_name.replace("-", "_")
    try:
        for d in sorted(sp.glob(f"{stem}-*.dist-info")):
            if d.is_dir():
                return d
    except OSError:
        return None
    return None


def map_provenance(sp: Path | None, name: str) -> dict:
    """Provenance d'UNE carte servie : `{name, sha, requested_ref, source, reason}`. Lecture locale d'un
    seul fichier, **zéro réseau**, **ne lève jamais**. `source` dit d'où vient la réponse — `vcs` (install
    git : le seul cas qui porte un SHA), `local-dir` (éditable/répertoire), `unknown` — et tout `sha=None`
    porte son `reason`. Un SHA faux coûte plus cher qu'un SHA manquant : il retire le doute qui aurait
    déclenché une vérification."""
    out: dict = {"name": name, "sha": None, "requested_ref": None, "source": "unknown", "reason": None}
    if sp is None:
        out["reason"] = "aucun venv d'outils ici (`cockpit tools install` n'a pas tourné)"
        return out
    dist = _dist_info(sp, name)
    if dist is None:
        out["reason"] = f"`{name}` n'est pas installée dans le venv d'outils"
        return out
    raw = _read_text(dist / "direct_url.json")
    if raw is None:
        out["reason"] = ("aucun direct_url.json : installée depuis un index ou un wheel, "
                         "PEP 610 n'enregistre alors pas d'origine")
        return out
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        out["reason"] = "direct_url.json présent mais illisible (JSON invalide)"
        return out
    vcs = info.get("vcs_info")
    if isinstance(vcs, dict) and isinstance(vcs.get("commit_id"), str):
        commit = vcs["commit_id"]
        if not _looks_like_sha(commit):
            out["reason"] = "vcs_info.commit_id présent mais n'est pas un SHA reconnaissable"
            return out
        out["sha"] = commit
        req = vcs.get("requested_revision")
        out["requested_ref"] = req if isinstance(req, str) else None
        out["source"] = "vcs"
        return out
    if isinstance(info.get("dir_info"), dict):
        out["source"] = "local-dir"
        out["reason"] = "installée depuis un répertoire local — aucun SHA à servir"
        return out
    out["reason"] = "direct_url.json sans `vcs_info` ni `dir_info`"
    return out


def maps_provenance(settings: Settings) -> list[dict]:
    """Les 3 cartes hôte que cette instance SERT, dans l'ordre de `MAP_REPOS`. Lecture locale, **zéro
    réseau**, **ne lève jamais** → utilisable depuis une sonde HTTP (`GET /api/version`) sans risque de 500
    ni d'attente. Dire ce qu'on SERT est local ; savoir si c'est à JOUR demande l'amont (`check_tools`)."""
    sp = site_packages(settings)
    return [map_provenance(sp, name) for name in MAP_REPOS]


# -- sonde de fraîcheur : comparaison à l'amont (réseau EXPLICITE, jamais au dispatch) ----------------
#
# La comparaison n'est PAS faite dans `preflight_tools` : ce chemin s'exécute avant CHAQUE spawn de worker,
# et y mettre 3 appels réseau rendrait le dispatch dépendant de GitHub — hors réseau il faudrait soit
# bloquer un dispatch sain, soit se taire (le faux-vert qu'on répare). Même partage que la fraîcheur du
# wheel (`build_provenance`) : le produit dit localement ce qu'il sert, la référence amont est explicite.
_LS_REMOTE_TIMEOUT_S = 30       # une réf, pas un clone : borné court (le dispatch, lui, ne l'appelle pas).


def check_plan(settings: Settings) -> list[dict]:
    """Étapes `{name, argv}` de la sonde : un `git ls-remote <url> <ref>` par carte (PUR — construit les
    argv, n'exécute rien). `ls-remote` ne transfère AUCUN objet : il rend la réf, pas l'historique."""
    return [{"name": name, "argv": ["git", "ls-remote", url, MAP_REF]}
            for name, url in MAP_REPOS.items()]


def parse_ls_remote(stdout: str, ref: str = MAP_REF) -> str | None:
    """Le SHA de `ref` dans une sortie `git ls-remote` (`<sha>\\trefs/heads/<ref>`), ou None si la réf n'y
    est pas / la ligne n'est pas exploitable. PUR. On n'accepte que ce qui a la FORME d'un SHA."""
    for line in stdout.splitlines():
        sha, _, name = line.partition("\t")
        sha = sha.strip()
        if name.strip() in (f"refs/heads/{ref}", ref) and _looks_like_sha(sha):
            return sha
    return None


def compare(served: list[dict], remote: Mapping[str, str | None]) -> list[dict]:
    """Fonction **PURE** (zéro I/O) : confronte le commit SERVI de chaque carte à celui de `MAP_REF` en
    amont. Les faits viennent de l'appelant (résolveurs injectables), comme `build_provenance.staleness`.

    Trois états, jamais un faux-vert : `up-to-date` · `differs` (les DEUX SHA écrits) · `unknown` (+ son
    `reason` : carte non installée, ou amont injoignable). **On ne dit jamais « en retard de N commits »** :
    `ls-remote` ne rend que des réfs, compter exigerait de rapatrier les objets. Un compte inventé retirerait
    le doute qui doit justement déclencher la vérification."""
    out: list[dict] = []
    for m in served:
        name = m["name"]
        upstream = remote.get(name)
        entry: dict = {"name": name, "served": m["sha"], "remote": upstream,
                       "state": "unknown", "reason": None}
        if m["sha"] is None:
            entry["reason"] = m["reason"]
        elif upstream is None:
            entry["reason"] = f"réf `{MAP_REF}` illisible en amont — comparaison impossible"
        else:
            entry["state"] = "up-to-date" if m["sha"] == upstream else "differs"
        out.append(entry)
    return out


def overall_state(entries: list[dict]) -> str:
    """L'état d'ensemble : `differs` dès qu'une carte diffère, sinon `unknown` dès qu'une n'a pas pu être
    comparée, sinon `up-to-date`. PUR. « Pas pu vérifier » ne se fond JAMAIS dans « à jour »."""
    states = {e["state"] for e in entries}
    if "differs" in states:
        return "differs"
    return "unknown" if "unknown" in states else "up-to-date"


def _symlink_sources(settings: Settings) -> dict[str, Path]:
    """Table `nom d'exécutable → source réelle` à exposer dans `tools/bin`. PUR (chemins seulement)."""
    venv_bin = tools_venv(settings) / "bin"
    node_bin = nodeenv_prefix(settings) / "bin"
    srcs: dict[str, Path] = {name: venv_bin / name for name in _VENV_BINS}
    srcs.update({name: node_bin / name for name in _NODE_BINS})
    return srcs


def install_plan(settings: Settings) -> list[dict[str, object]]:
    """Étapes ordonnées `{name, argv}` de l'install (PUR — construit les argv, n'exécute rien) : les 3 cartes
    (`git+<url>@<ref>`) + les outils qualité py, puis l'**épinglage forcé** des cartes, puis nodeenv, Node.

    **Pourquoi DEUX passes sur les cartes** — le piège pip-git-SHA, vu en vrai sur la VM 9311 le 2026-08-03 :
    `pip install --upgrade git+<url>@main` clone, **résout `main` au bon commit**, prépare les métadonnées…
    puis **saute l'install** parce que la version installée est identique. Les cartes sont figées à `0.1.0`,
    donc la version ne discrimine JAMAIS : cette commande ne remet rien à niveau et rend pourtant rc 0. Un
    `cockpit tools install` répondait « 🟢 » sans avoir bougé une ligne — le faux-vert exact que la sonde
    `check_tools` a attrapé (elle est restée rouge après le « remède », et c'est comme ça qu'on l'a su).

    La 1ʳᵉ passe reste `--upgrade` : c'est elle qui résout les **dépendances** (correcte sur une install
    fraîche). La 2ᵈᵉ force le **code des cartes** à la réf demandée sans retoucher aux deps
    (`--force-reinstall --no-deps`) — même parade que le cutover de `mcp-catalogs`. Retirer la 2ᵈᵉ passe
    rétablit le no-op silencieux ; un test la verrouille."""
    pip = str(tools_venv(settings) / "bin" / "pip")
    map_specs = [f"git+{url}@{MAP_REF}" for url in MAP_REPOS.values()]
    return [
        {"name": "pip-tools", "argv": [pip, "install", "--upgrade", *map_specs, *PY_QUALITY]},
        {"name": "pip-maps-pin",
         "argv": [pip, "install", "--force-reinstall", "--no-deps", *map_specs]},
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


def check_tools(settings: Settings, *, runner: Runner | None = None) -> dict:
    """Sonde de fraîcheur des 3 cartes servies (IMPUR : un `git ls-remote` par carte, via le runner injecté).
    **Ne lève pas** — un amont injoignable devient un état `unknown` porteur de sa raison, jamais une
    exception ni un vert. Clone ANONYME (`anonymous_env`) : les cartes sont publiques, aucun credential
    n'entre ici. Retour `{ref, state, maps:[{name, served, remote, state, reason}]}`."""
    from cockpit.core.run import RunError, RunTimeout
    runner = runner or _default_runner
    served = maps_provenance(settings)
    # Une carte qu'on ne sert PAS n'a rien à comparer : la raison est déjà locale. Ne pas interroger l'amont
    # pour elle évite d'attendre le réseau (jusqu'au timeout, hors ligne) pour une réponse déjà connue.
    comparable = {m["name"] for m in served if m["sha"] is not None}
    env = anonymous_env()
    remote: dict[str, str | None] = {}
    for step in check_plan(settings):
        name = str(step["name"])
        if name not in comparable:
            continue
        try:
            r = runner(step["argv"], env=env, timeout=_LS_REMOTE_TIMEOUT_S)
        except (RunTimeout, RunError, OSError):
            remote[name] = None
            continue
        remote[name] = parse_ls_remote(r.stdout) if r.ok else None
    entries = compare(served, remote)
    return {"ref": MAP_REF, "state": overall_state(entries), "maps": entries}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit tools <install|check>`. Aucun credential sur aucune des deux — les 3 cartes sont
    publiques, le clone est anonyme (le `--token-file` d'avant a été RETIRÉ, pas rendu optionnel : un
    drapeau accepté-et-ignoré ferait croire qu'il sert encore)."""
    if getattr(args, "action", None) == "check":
        return _cli_check(settings)
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


_CHECK_MARKS = {"up-to-date": "🟢", "differs": "🔴", "unknown": "🟡"}
# Trois issues DISTINCTES, parce que « je n'ai pas pu vérifier » n'est ni « à jour » ni « périmé » : le
# confondre avec l'un des deux serait exactement le faux-vert (ou le faux-rouge) que cette sonde répare.
_CHECK_EXITS = {"up-to-date": 0, "differs": 1, "unknown": 2}


def _cli_check(settings: Settings) -> int:
    """Rend l'écart entre les cartes SERVIES et `MAP_REF` en amont. Sortie **1** si au moins une diffère,
    **2** si aucune ne diffère mais qu'au moins une n'a pas pu être comparée, **0** seulement quand les
    trois sont vérifiées à jour. Le geste de remise à niveau est EXPLICITE (`cockpit tools install`,
    idempotent) : cette commande rapporte, elle ne mute rien."""
    report = check_tools(settings)
    for e in report["maps"]:
        mark = _CHECK_MARKS.get(e["state"], "🟡")
        if e["state"] == "differs":
            detail = f"servie {e['served'][:12]} · amont {e['remote'][:12]} — DIFFÈRE"
        elif e["state"] == "up-to-date":
            detail = f"servie {e['served'][:12]} — à jour"
        else:
            detail = f"non comparée ({e['reason']})"
        print(f"  {mark} {e['name']:<10} {detail}")
    state = str(report["state"])
    if state == "differs":
        print(f"🔴 au moins une carte diffère de `{report['ref']}` — `cockpit tools install` les remet à "
              f"niveau (idempotent). Le nombre de commits d'écart n'est pas mesurable sans rapatrier "
              f"l'historique : la sonde dit LESQUELLES ont bougé, pas de combien.")
    elif state == "unknown":
        print("🟡 fraîcheur NON vérifiée (amont injoignable ou carte absente) — ce n'est pas un vert.")
    else:
        print(f"🟢 les {len(report['maps'])} cartes servies sont à jour sur `{report['ref']}`.")
    return _CHECK_EXITS.get(state, 2)
