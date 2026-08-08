"""tools — provisionnement **hôte-niveau** de l'outillage que les bundles DÉCLARENT (les 3 cartes + Node +
qualité py), dans un venv d'outils dédié sous `$FORGEMASTER_HOME/tools/`, exposé sur un **unique** `tools/bin`
que le dispatch worker ET le gate natif préfixent au PATH. Ferme le fossé « déclaré → présent » (P0 de
l'épic tooling-fulfillment) : `bundles/base/CLAUDE.md` promet `codemap`/`docsmap`/`frontmap`/`node`/`ruff`…
mais le wheel n'expose que `forgemaster` (`codemap` n'est qu'un module `-m codemap`), les cartes voisines ne
sont
que *clonées* (jamais pip-installées), Node n'est provisionné nulle part, et le worker spawn en `env=None`
(PATH systemd minimal, hérité passif) — même présents, il ne les verrait pas.

`taskmap` n'est PAS provisionné ici : ce n'est pas une carte de contenu par-projet (comme codemap/docsmap/
frontmap) mais le **moteur d'ordonnancement central** (`taskmap.graph`), importé en-process par
`roadmap/resolver.py`. La lib est fournie autrement (vendorée au wheel par `deploy/build-wheel.sh` ; editable
en dev via `webbuild.ensure_maps`) ; aucun projet n'a de task-graph local à mapper → pas de CLI host exposé.

Conception (mêmes conventions que `dispatch`/`codemap`) : des seams **PURS** testables sans subprocess —
`tools_bin`/`tools_env` (composition PATH), `install_plan` (quels paquets, quel venv, quels symlinks : les
argv pip/nodeenv) ; l'exécution passe par un `runner` **injecté**. Le pip/nodeenv réel est prouvé à la
vérif install fraîche, jamais au gate déterministe.

Pas dans le venv du forgemaster (ne pas polluer ses deps ni son PATH systemd) : un venv **séparé** sous
FORGEMASTER_HOME. Node via **nodeenv** (rootless, pip-natif) → autonome, marche en portée `--user` sans sudo.

**Les 3 cartes viennent de l'ÉDITION, plus d'une réf mobile** (2026-08-08). Jusqu'ici elles étaient tirées de
`git+https://…@main` : deux installs à une semaine d'écart posaient deux produits sous le même numéro de
version, et le critère de l'édition (« deux installs de la même édition posent exactement le même code »)
était donc invérifiable pour elles. `deploy/build-wheel.sh` les bâtit désormais au SHA du sibling et les
embarque dans le wheel (`forgemaster/_maps` : 3 wheels + `maps.json`) ; l'install est **hors-ligne**
(`--no-index`, des chemins de fichiers) et **épinglée**. Ce qui disparaît avec la réf : le chemin git entier,
donc AUCUN clone, donc plus aucune surface où un credential pourrait entrer — la propriété n'est plus tenue
par un env de précaution mais par l'absence du chemin.

`toolchain check` suit le même mouvement : il comparait le commit servi au `main` amont (`git ls-remote`) ;
il compare désormais le commit **servi** au commit que l'**édition installée déclare**. Zéro réseau, exact,
et il répond à la question qui reste ouverte tant que `update apply` ne repose pas les cartes — *cette
instance sert-elle les cartes de son édition ?* La question « mon édition est-elle en retard ? » est celle
du **wheel**, portée par `build_provenance`.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.core.run import RunResult, run

Runner = Callable[..., RunResult]


class ToolPreflightError(RuntimeError):
    """Un binaire déclaré par la facette active (`allowedTools`) ne résout pas sur le PATH du worker.
    Levé AVANT le spawn (fail-loud, actionnable) : le worker ne découvre plus l'absence à l'usage."""


class EditionMapsError(RuntimeError):
    """L'édition installée ne porte pas les 3 cartes posables (`forgemaster/_maps` absent, illisible ou
    incomplet). **Fail-loud, jamais un repli git** : quel mode d'install est actif est une question qui se
    RÉPOND (phase 4·3), elle ne se pré-répond pas ici par une cascade silencieuse vers une réf mobile."""

# Les 3 cartes de contenu par-projet, packagées (console_scripts codemap/docsmap/frontmap). Posées depuis les
# wheels que l'ÉDITION embarque (`forgemaster/_maps`) — plus aucun clone, donc aucun credential possible.
# L'URL reste l'IDENTITÉ de chaque carte (d'où elle vient, ce qu'on lit dans un rapport), pas une source
# d'install. (task-map exclu : moteur central importé en-process, pas une carte host — cf. docstring.)
MAP_REPOS: dict[str, str] = {
    "code-map": "https://github.com/Avadis7860/code-map.git",
    "docs-map": "https://github.com/Avadis7860/docs-map.git",
    "front-map": "https://github.com/Avadis7860/front-map.git",
}
# Le manifeste de l'édition, écrit par `deploy/build-wheel.sh` à côté des 3 wheels.
EDITION_MAPS_DIR = "_maps"
EDITION_MANIFEST = "maps.json"
# Le tampon posé DANS chaque paquet de carte : après `pip install`, il vit sous `<site-packages>/<pkg>/` et
# décrit donc CE QUI EST INSTALLÉ — pas ce que l'édition prétend avoir posé.
VENDORED_FROM = "_vendored_from.txt"
# Outils qualité Python (extra `forgemaster[dev]`, NON tirés par `pip install <wheel>` — deps runtime seules).
PY_QUALITY: tuple[str, ...] = ("ruff", "pytest", "mypy")
# Node LTS via nodeenv (rootless) — prefix autonome sous tools/nodeenv, ses bin symlinkés dans tools/bin.
NODE_VERSION = "lts"
# Exécutables exposés sur tools/bin — ceux que le worker et le gate résolvent (par-type : le worker n'utilise
# que ceux de sa facette, mais on expose tout une fois ; le preflight P1 vérifie la présence par-facette).
_VENV_BINS: tuple[str, ...] = ("codemap", "docsmap", "frontmap", "ruff", "pytest", "mypy")
_NODE_BINS: tuple[str, ...] = ("node", "npm", "npx")
# Catalogue des outils que l'HÔTE provisionne (`forgemaster toolchain install` → `tools/bin`) : ceux-là
# DOIVENT
# préexister au dispatch → le preflight les gate. Les autres binaires qu'une facette déclare (`eslint`,
# `tsc`, `vitest`, `pip install`…) sont **projet-locaux** (le worker les installe via `npm install`/le venv
# projet) → jamais dans `tools/bin`, jamais gatés (sinon on bloquerait un worktree neuf à tort).
HOST_TOOLS: frozenset[str] = frozenset((*_VENV_BINS, *_NODE_BINS))

_STEP_TIMEOUT_S = 900   # pip (clone git + build) / nodeenv (download Node) : lents mais bornés (fail-loud).


# -- seams PURS (composition de chemins / PATH — zéro subprocess) ------------------------------------

def tools_root(settings: Settings) -> Path:
    """Racine de l'outillage hôte-niveau : `$FORGEMASTER_HOME/tools/`."""
    return settings.home / "tools"


def tools_venv(settings: Settings) -> Path:
    """Venv Python DÉDIÉ des outils (séparé du venv forgemaster) : `tools/venv`."""
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
    """`tools_env` PRÉFIXÉ du bin du venv COURANT (là où vit le script `forgemaster`). Pour les surfaces où un
    humain / `claude` invoque `forgemaster …` DIRECTEMENT — l'interview (`interview.interview_env`) et le
    **terminal web** (`pty.shell_env`) : `forgemaster` n'est PAS dans `tools/bin` (le worker et le gate n'en
    ont
    pas besoin, eux). Sans ce préfixe, la surface hérite d'un PATH (shell de login / systemd minimal) sans
    `forgemaster` → `forgemaster interview`/`forgemaster roadmap …` en `command not found`. PUR."""
    env = tools_env(settings, base=base)
    forgemaster_bin = str(Path(sys.executable).parent)          # /…/venv/bin — porte le script `forgemaster`
    env["PATH"] = os.pathsep.join([forgemaster_bin, env["PATH"]])
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
            f"pose-les (`forgemaster toolchain install`) puis relance. worktree={worktree}")


# -- provenance de l'outillage SERVI (lecture LOCALE, zéro réseau, ne lève jamais) --------------------
#
# Une instance pose les 3 cartes au provisioning et rien ne les re-synchronise ensuite ; `preflight_tools`
# ne teste qu'une PRÉSENCE. Elle sert donc des cartes dont l'identité doit être LISIBLE, sans quoi personne
# ne peut dire ce qui tourne là. Deux sources, dans cet ordre :
#   1. le tampon `_vendored_from.txt` posé DANS le paquet par `build-wheel.sh` — le mode canonique depuis le
#      2026-08-08 (les cartes viennent des wheels de l'édition, et un wheel n'a pas de `vcs_info`) ;
#   2. `direct_url.json` (PEP 610), que pip pose à l'install git avec le `commit_id` RÉSOLU — le mode
#      historique, encore vivant sur toute instance provisionnée avant cette date.
# On LIT ce qui existe, on n'écrit aucun registre parallèle. Même contrat de dégradation que
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


def venv_site_packages(venv: Path) -> Path | None:
    """Le `site-packages` d'UN venv quelconque (`<venv>/lib/python*/site-packages`), ou None. Ne lève pas.
    Paramétré par le venv (et non par `Settings`) parce que cet hôte en porte plusieurs, de cycles de vie
    distincts : celui des outils, et celui du serveur MCP co-installé (`mcp.local`)."""
    try:
        for p in sorted((venv / "lib").glob("python*/site-packages")):
            if p.is_dir():
                return p
    except OSError:
        return None
    return None


def site_packages(settings: Settings) -> Path | None:
    """Le `site-packages` du venv d'OUTILS, ou None s'il n'existe pas — cas NORMAL d'un forgemaster en
    checkout
    dev où `tools install` n'a jamais tourné. Ne lève pas."""
    return venv_site_packages(tools_venv(settings))


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


def _stamped_sha(sp: Path, dist: Path) -> str | None:
    """Le SHA du tampon `_vendored_from.txt` posé DANS le paquet par `build-wheel.sh`, ou None. Ne lève pas.

    Le fichier est localisé par le **`RECORD`** de la distribution — jamais par un nom de paquet deviné
    depuis le nom de distribution : `code-map` → `codemap` marche, mais c'est une convention, pas une règle
    (PEP 503 normalise le nom de DISTRIBUTION, il ne dit rien du nom d'IMPORT). Le RECORD, lui, est exigé par
    le format wheel et énumère exactement ce que l'install a posé."""
    raw = _read_text(dist / "RECORD")
    if raw is None:
        return None
    for row in csv.reader(raw.splitlines()):
        if row and row[0].endswith(f"/{VENDORED_FROM}"):
            stamp = (_read_text(sp / row[0]) or "").strip()
            return stamp if _looks_like_sha(stamp) else None
    return None


def dist_provenance(sp: Path | None, name: str) -> dict:
    """Provenance d'UNE distribution installée : `{name, sha, requested_ref, source, reason}`. Lecture
    locale, **zéro réseau**, **ne lève jamais**. `source` dit d'où vient la réponse — `edition` (le tampon
    posé dans le paquet par `build-wheel.sh`), `vcs` (install git), `local-dir` (éditable/répertoire),
    `unknown` — et tout `sha=None` porte son `reason`. Un SHA faux coûte plus cher qu'un SHA manquant : il
    retire le doute qui aurait déclenché une vérification.

    **Le tampon est lu EN PREMIER, et ce n'est pas un ordre de convenance** : une carte posée depuis un
    wheel de l'édition n'a plus de `vcs_info` (PEP 610 n'enregistre alors qu'un `archive_info`, sans SHA
    git), donc s'en tenir à PEP 610 rendrait `sha=None` sur exactement le mode qu'on vient de rendre
    canonique. La cascade PEP 610 reste **derrière**, inchangée : une instance encore installée en
    `git+…@main` continue de répondre `vcs`, et c'est précisément ce qui distingue les deux modes.

    Nommée `dist_*` et non `map_*` : elle ne lit rien de spécifique aux cartes — un `.dist-info`, et rien
    d'autre. Le serveur MCP co-installé (`mcp.local.server_provenance`) l'appelle telle quelle, plutôt
    que d'entretenir une seconde lecture du même format qui divergerait au premier cas tordu."""
    out: dict = {"name": name, "sha": None, "requested_ref": None, "source": "unknown", "reason": None}
    if sp is None:
        out["reason"] = "aucun venv d'outils ici (`forgemaster toolchain install` n'a pas tourné)"
        return out
    dist = _dist_info(sp, name)
    if dist is None:
        out["reason"] = f"`{name}` n'est pas installée dans le venv d'outils"
        return out
    stamp = _stamped_sha(sp, dist)
    if stamp is not None:
        out["sha"], out["source"] = stamp, "edition"
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
    ni d'attente. Dire ce qu'on SERT est une chose ; savoir si c'est **ce que l'édition déclare** en est une
    autre, et c'est `check_tools` — local lui aussi désormais."""
    sp = site_packages(settings)
    return [dist_provenance(sp, name) for name in MAP_REPOS]


# -- l'édition posable : les 3 wheels de cartes embarqués dans le wheel (lecture locale, zéro réseau) ----


def edition_maps_dir() -> Path:
    """Le dossier `forgemaster/_maps` du forgemaster INSTALLÉ (3 wheels de cartes + `maps.json`), tel que
    `deploy/build-wheel.sh` l'a embarqué. Composition de chemin, **PUR** : le dossier peut ne pas exister —
    c'est le cas NORMAL d'un checkout dev/editable, et `read_edition` le dit alors franchement."""
    return Path(__file__).resolve().parent / EDITION_MAPS_DIR


def read_edition(maps_dir: Path | None = None) -> list[dict]:
    """Ce que l'édition installée DÉCLARE : `[{name, wheel, sha, committed_at}]` dans l'ordre de
    `MAP_REPOS`. Lève `EditionMapsError` — jamais un repli, jamais une liste vide muette.

    Trois refus distincts, parce qu'ils n'ont pas le même remède : dossier absent (wheel dégradé ou install
    editable) · manifeste illisible · une carte de `MAP_REPOS` non déclarée ou dont le wheel manque (une
    édition **amputée** poserait 2 cartes sur 3 en rendant rc 0, exactement le demi-provisioning que
    `install_tools` refuse déjà ailleurs)."""
    d = Path(maps_dir) if maps_dir is not None else edition_maps_dir()
    raw = _read_text(d / EDITION_MANIFEST)
    if raw is None:
        raise EditionMapsError(
            f"l'édition installée ne porte pas les cartes ({d / EDITION_MANIFEST} absent) — un wheel de "
            f"release les embarque (`deploy/build-wheel.sh`). En checkout dev, les cartes viennent des "
            f"siblings éditables, pas de ce chemin.")
    try:
        par_nom = {m["name"]: {"name": m["name"], "wheel": m["wheel"], "sha": m.get("sha"),
                               "committed_at": m.get("committed_at")}
                   for m in json.loads(raw)["maps"]}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        # Le manifeste est décodé ENTIÈREMENT ici, champs compris : un `KeyError` qui s'échapperait plus bas
        # remonterait BRUT à `check_tools`, dont le contrat est de ne jamais lever.
        raise EditionMapsError(f"{d / EDITION_MANIFEST} illisible ({exc}) — édition non exploitable") from exc
    out: list[dict] = []
    for name in MAP_REPOS:
        m = par_nom.get(name)
        if m is None:
            raise EditionMapsError(f"l'édition ne déclare pas `{name}` — édition AMPUTÉE, on ne pose pas "
                                   f"{len(par_nom)} cartes sur {len(MAP_REPOS)} en rendant vert")
        if not (d / m["wheel"]).is_file():
            raise EditionMapsError(f"`{name}` est déclarée mais son wheel manque ({d / m['wheel']}) — "
                                   f"édition incohérente")
        out.append(m)
    return out


def compare(served: list[dict], attendu: Mapping[str, str | None]) -> list[dict]:
    """Fonction **PURE** (zéro I/O) : confronte le commit SERVI de chaque carte à celui que l'ÉDITION
    installée déclare. Les faits viennent de l'appelant (résolveurs injectables), comme
    `build_provenance.staleness`.

    Trois états, jamais un faux-vert : `up-to-date` · `differs` (les DEUX SHA écrits) · `unknown` (+ son
    `reason` : carte non installée, ou édition muette sur elle). **On ne dit jamais « en retard de N
    commits »** : deux SHA ne se soustraient pas sans l'historique, et un compte inventé retirerait le doute
    qui doit justement déclencher la vérification."""
    out: list[dict] = []
    for m in served:
        name = m["name"]
        cible = attendu.get(name)
        entry: dict = {"name": name, "served": m["sha"], "edition": cible,
                       "state": "unknown", "reason": None}
        if m["sha"] is None:
            entry["reason"] = m["reason"]
        elif cible is None:
            entry["reason"] = "l'édition installée ne déclare rien pour cette carte — comparaison impossible"
        else:
            entry["state"] = "up-to-date" if m["sha"] == cible else "differs"
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


def install_plan(settings: Settings, *, maps_dir: Path | None = None) -> list[dict[str, object]]:
    """Étapes ordonnées `{name, argv}` de l'install (PUR au sens du subprocess — construit les argv, n'exécute
    rien ; il LIT le manifeste de l'édition) : les 3 cartes depuis leurs **wheels embarqués**, puis les outils
    qualité py, puis nodeenv, puis Node. Lève `EditionMapsError` si l'édition n'est pas posable.

    **Les cartes en PREMIER, et c'est délibéré** : c'est la seule étape hors-ligne. Quand le réseau manque,
    les 3 cartes sont posées et l'échec porte le nom de ce qui exigeait vraiment le réseau, au lieu de tout
    faire tomber d'un bloc.

    **`--no-index`** : la garantie hors-ligne est dans l'argv, pas dans une intention — pip ne peut pas
    « compléter » depuis PyPI une carte qu'il trouverait insuffisante.

    **`--force-reinstall` reste, le piège pip no-op a survécu au changement de source.** Vu en vrai sur la
    VM 9311 le 2026-08-03 avec `git+…@main` : pip résout, prépare les métadonnées, puis **saute l'install**
    parce que la version installée est identique. Les cartes sont figées à `0.1.0`, donc la version ne
    discrimine JAMAIS — fichier ou pas. Sans ce drapeau, `toolchain install` rend « 🟢 » sans avoir bougé une
    ligne. Un test le verrouille.

    **`--no-deps` n'est PAS repris** (il l'était sur la 2ᵈᵉ passe git, pour ne pas retoucher aux deps déjà
    résolues) : les 3 cartes ont `dependencies = []` aujourd'hui, et le jour où l'une en gagne une, une
    install hors-ligne doit **échouer bruyamment** plutôt que poser une carte amputée en rendant rc 0."""
    pip = str(tools_venv(settings) / "bin" / "pip")
    d = Path(maps_dir) if maps_dir is not None else edition_maps_dir()
    wheels = [str(d / m["wheel"]) for m in read_edition(d)]
    return [
        {"name": "pip-maps", "argv": [pip, "install", "--no-index", "--force-reinstall", *wheels]},
        {"name": "pip-quality", "argv": [pip, "install", "--upgrade", *PY_QUALITY]},
        {"name": "pip-nodeenv", "argv": [pip, "install", "--upgrade", "nodeenv"]},
        {"name": "nodeenv", "argv": [str(tools_venv(settings) / "bin" / "nodeenv"),
                                     f"--node={NODE_VERSION}", "--force", str(nodeenv_prefix(settings))]},
    ]


# -- exécution (IMPUR : subprocess via runner injecté ; symlinks) ------------------------------------

def _default_runner(argv: list[str], *, env: Mapping[str, str] | None, timeout: float) -> RunResult:
    return run(argv, env=env, timeout=timeout, check=False)


def anonymous_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """L'env d'un clone git de pip : l'ambiant, **sans aucun credential**, et `GIT_TERMINAL_PROMPT=0`.

    Le seam existe pour que ça reste vrai : il ne compose aucun `url.…insteadOf`, donc aucun token ne peut
    se glisser dans l'env d'un enfant git (le test l'asserte). Le `GIT_TERMINAL_PROMPT=0` n'est pas
    décoratif — un repo renommé ou re-privatisé fait *pendre* git sur un prompt jusqu'au timeout, au lieu de
    rendre une erreur lisible.

    **Son consommateur n'est plus `install_tools`** (2026-08-08) : les cartes viennent de l'édition, plus
    d'un clone, donc ce chemin-là n'a plus de git à garder. Le co-install du serveur MCP (`mcp.local`), lui,
    clone toujours — le seam reste vivant pour lui, chez son seul appelant réel."""
    env = dict(base if base is not None else os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def install_tools(settings: Settings, *, runner: Runner | None = None,
                  maps_dir: Path | None = None) -> dict:
    """Provisionne l'outillage hôte-niveau (IDEMPOTENT, FAIL-LOUD). Crée le venv d'outils, installe les 3
    cartes **depuis les wheels de l'édition** (hors-ligne) + qualité py + Node (nodeenv), puis symlinke
    chaque exécutable dans `tools/bin`. Une étape rouge (rc≠0) **abandonne** (jamais un demi-provisioning)
    et retourne `{ok:False, steps, error}`. Retour
    `{ok, steps:[{name, ok, exit_code, error?}], symlinks:[nom]}`.

    Plus aucun credential ne peut entrer ici, et ce n'est plus tenu par un env de précaution mais par
    l'absence du chemin : il n'y a plus d'URL dans le plan, donc plus de clone.
    """
    runner = runner or _default_runner
    root = tools_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"ok": True, "steps": [], "symlinks": []}
    steps: list[dict] = report["steps"]  # type: ignore[assignment]

    # 1. venv d'outils (idempotent : `python -m venv` sur un venv existant est sûr).
    venv_step = {"name": "venv", "argv": [sys.executable, "-m", "venv", str(tools_venv(settings))]}
    if not run_step(runner, venv_step, env=dict(os.environ), steps=steps):
        report["ok"] = False
        report["error"] = "création du venv d'outils échouée"
        return report

    # 2. env : l'ambiant, tel quel. Le `GIT_TERMINAL_PROMPT=0` d'avant gardait les clones git de pip ; il
    #    n'y a plus de clone ici, donc plus rien à garder (il vit toujours chez `mcp.local`, qui clone).
    env = dict(os.environ)

    # 3. installs (fail-loud : abandon au 1er rouge). Le plan LIT l'édition — une édition non posable est un
    #    refus AVANT toute écriture, pas un demi-provisioning.
    try:
        plan = install_plan(settings, maps_dir=maps_dir)
    except EditionMapsError as exc:
        report["ok"] = False
        report["error"] = str(exc)
        return report
    for step in plan:
        if not run_step(runner, step, env=env, steps=steps):
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


def run_step(runner: Runner, step: dict, *, env: Mapping[str, str], steps: list[dict],
             timeout: float = _STEP_TIMEOUT_S) -> bool:
    """Lance une étape `{name, argv}` via le runner, journalise le résultat dans `steps`, retourne `ok`.
    **Ne lève pas** : une erreur de transport (binaire absent, timeout…) devient un step rouge porteur de
    son motif — c'est ce qui distingue « l'étape a échoué » de « le provisioning a explosé ».

    Public (et non `_run_step`) parce que le co-install du serveur MCP (`mcp.local`) déroule exactement la
    même mécanique : sans ça, il ré-implémenterait la partie facile (le rc) en oubliant la partie qui
    compte (les exceptions de transport)."""
    from forgemaster.core.run import RunError, RunTimeout
    entry: dict = {"name": step["name"]}
    try:
        r = runner(step["argv"], env=env, timeout=timeout)
        entry.update(ok=r.ok, exit_code=r.returncode)
        if not r.ok:
            entry["error"] = (r.stderr.strip() or r.stdout.strip())[:300]
    except (RunTimeout, RunError, OSError) as exc:
        entry.update(ok=False, exit_code=None, error=f"{type(exc).__name__}: {exc}"[:300])
    steps.append(entry)
    return bool(entry["ok"])


def check_tools(settings: Settings, *, maps_dir: Path | None = None) -> dict:
    """L'instance sert-elle les cartes de son ÉDITION ? Lecture **strictement locale, zéro réseau, zéro
    subprocess** — **ne lève pas** : une édition illisible devient `unknown` porteur de sa raison, jamais une
    exception ni un vert. Retour `{edition_dir, reason, state, maps:[{name, served, edition, state,
    reason}]}` — `edition_dir` est **où** le manifeste a été lu (diagnostic), l'`edition` de chaque entrée
    est le **SHA** qu'il déclare pour cette carte.

    Elle comparait le commit servi au `main` amont (`git ls-remote`). Ce n'était pas la bonne question une
    fois les cartes épinglées : « suis-je en retard sur upstream ? » est la question du **wheel** (portée par
    `build_provenance`, puis par le canal servi de la phase 5). Celle qui reste, et qui n'avait aucune
    réponse, est **« mes cartes sont-elles celles de mon édition ? »** — elle se pose exactement quand une
    instance a monté d'édition sans reposer son outillage, et elle se répond sans réseau."""
    served = maps_provenance(settings)
    d = Path(maps_dir) if maps_dir is not None else edition_maps_dir()
    attendu: dict[str, str | None] = {}
    note: str | None = None
    try:
        attendu = {m["name"]: m.get("sha") for m in read_edition(d)}
    except EditionMapsError as exc:
        note = str(exc)
    entries = compare(served, attendu)
    return {"edition_dir": str(d) if note is None else None, "reason": note,
            "state": overall_state(entries), "maps": entries}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `forgemaster toolchain <install|check>`. Aucun credential sur aucune des deux — les cartes
    viennent des wheels de l'édition, il n'y a plus de clone du tout (le `--token-file` d'avant a été RETIRÉ,
    pas rendu optionnel : un drapeau accepté-et-ignoré ferait croire qu'il sert encore)."""
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
    """Rend l'écart entre les cartes SERVIES et celles que l'ÉDITION installée déclare. Sortie **1** si au
    moins une diffère, **2** si aucune ne diffère mais qu'au moins une n'a pas pu être comparée, **0**
    seulement quand les trois sont vérifiées conformes. Le geste de remise à niveau est EXPLICITE
    (`forgemaster toolchain install`, idempotent, hors-ligne) : cette commande rapporte, elle ne mute rien."""
    report = check_tools(settings)
    for e in report["maps"]:
        mark = _CHECK_MARKS.get(e["state"], "🟡")
        if e["state"] == "differs":
            detail = f"servie {e['served'][:12]} · édition {e['edition'][:12]} — DIFFÈRE"
        elif e["state"] == "up-to-date":
            detail = f"servie {e['served'][:12]} — conforme"
        else:
            detail = f"non comparée ({e['reason']})"
        print(f"  {mark} {e['name']:<10} {detail}")
    state = str(report["state"])
    if state == "differs":
        print("🔴 au moins une carte n'est pas celle de cette édition — `forgemaster toolchain install` "
              "la repose (idempotent, hors-ligne). Le nombre de commits d'écart n'est pas mesurable sans "
              "l'historique : la sonde dit LESQUELLES diffèrent, pas de combien.")
    elif state == "unknown":
        print(f"🟡 conformité NON vérifiée — ce n'est pas un vert. {report['reason'] or ''}".rstrip())
    else:
        print(f"🟢 les {len(report['maps'])} cartes servies sont celles de l'édition installée.")
    return _CHECK_EXITS.get(state, 2)
