"""check — gate de **complétude** d'une roadmap : une roadmap est *opérationnelle* (drainable par
l'orchestrateur) ssi chaque task porte une DoD, chaque dépendance existe, aucun cycle, et chaque feature
cible une facette connue du bundle du projet.

Réutilise l'**autorité de séquencement** (`resolver.classify` : dangling → ERROR, cycle → CYCLE) — zéro
réécriture du DAG — et le vocab de facettes registre-driven (`model._project_facets`). Déterministe,
read-only ; **sémantique de gate** (exit 1 dès une issue). C'est l'unique autorité de complétude, partagée
par le chemin CLI et l'API (aucune contrainte n'est durcie côté `model`/HTTP, qui restent souples).
"""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.git.internal import GitOpError, InternalGit
from forgemaster.projects.registry import get_project, sot_path_for
from forgemaster.provision import BundleError, load_bundle, read_bundle_manifest
from forgemaster.roadmap import model, resolver

# catalogue archétype→axes, semé en base (bundle source)
_DEPTH_AXES_PATH = ".forgemaster/depth-axes.yaml"
# reports worker {axe: raison}, per-projet (worktree/SoT)
_DEFERRED_AXES_PATH = ".forgemaster/deferred-axes.yaml"


@dataclass(frozen=True)
class Issue:
    """Un défaut de complétude localisé (feature, éventuellement task) avec son motif lisible."""
    kind: str            # DANGLING_DEP | CYCLE | MISSING_ACCEPTANCE | BAD_FACET | MISSING_FACET | EMPTY
    #                    | DANGLING_FEATURE_DEP | FEATURE_CYCLE | DEAD_FEATURE_DEP  (inter-feature, v10)
    #                    | UNCOVERED_AXIS  (gate de profondeur par archétype, opt-in `--depth` ; task=axe)
    feature: str
    task: str | None
    detail: str


def check_roadmap(conn: sqlite3.Connection, project_slug: str) -> list[Issue]:
    """Liste les défauts qui empêchent une roadmap d'être opérationnelle (liste vide = drainable).
    Read-only ; lève `KeyError`/`ValueError` si le projet est inconnu."""
    project = get_project(conn, project_slug)
    valid_facets = model._project_facets(project)
    features = model.list_features(conn, project_slug)
    if not features:
        return [Issue("EMPTY", "-", None, f"projet {project_slug} : aucune feature")]
    issues: list[Issue] = []
    for f in features:
        fslug = f["slug"]
        facet = f.get("facet")           # facette de dispatch : explicite ET connue du bundle
        if facet is None:
            issues.append(Issue("MISSING_FACET", fslug, None,
                                "feature sans facette (dispatch non aligné sur une étape)"))
        elif facet not in valid_facets:
            issues.append(Issue("BAD_FACET", fslug, None,
                                f"facette {facet!r} hors vocab du bundle : {sorted(valid_facets)}"))
        tasks = model.list_tasks(conn, f["id"])
        if not tasks:
            issues.append(Issue("EMPTY", fslug, None, "feature sans task"))
            continue
        classified = resolver.classify({t["slug"]: t for t in tasks})
        for slug, t in classified.items():
            if t["state"] == "ERROR":
                issues.append(Issue("DANGLING_DEP", fslug, slug,
                                    f"dépendance inexistante : {', '.join(t['blockers'])}"))
            elif t["state"] == "CYCLE":
                issues.append(Issue("CYCLE", fslug, slug, "task dans un cycle de dépendances"))
            if not (t.get("acceptance") or "").strip():
                issues.append(Issue("MISSING_ACCEPTANCE", fslug, slug,
                                    "task sans critère de DoD (acceptance)"))
    # DAG INTER-feature (v10) : dangling / cycle (autorité taskmap, comme le DAG des tasks) + deadlock
    # cancelled-dep (surfacé, jamais bloqué en silence). Une feature BLOCKED_DEPS normale (prérequis pas
    # encore mergé) n'est PAS une issue — c'est l'ordre de lancement qui se déroule.
    feat_states = resolver.classify_features(conn, project_slug)
    by_status = {f["slug"]: f["status"] for f in features}
    for fslug, fc in feat_states.items():
        if fc["state"] == "ERROR":
            issues.append(Issue("DANGLING_FEATURE_DEP", fslug, None,
                                f"feature prérequise inexistante : {', '.join(fc['blockers'])}"))
        elif fc["state"] == "CYCLE":
            issues.append(Issue("FEATURE_CYCLE", fslug, None,
                                "feature dans un cycle de dépendances inter-features"))
        dead = [d for d in fc["depends_on"] if by_status.get(d) == "cancelled"]
        if dead:
            issues.append(Issue("DEAD_FEATURE_DEP", fslug, None,
                                f"dépend d'une feature annulée (jamais débloquable) : {', '.join(dead)}"))
    return issues


# --- Gate de PROFONDEUR par archétype (séparé de `check_roadmap`, qui reste la complétude structurelle) ---
# `check_roadmap` prouve qu'une roadmap est *drainable* (DoD/DAG/facettes) ; `check_depth_axes` prouve qu'elle
# est *complète pour son archétype* (jeu/outil/service/app/doc). Opt-in (`roadmap check --depth`) : gate de
# fin-d'interview/release, pas un lint per-édit. Manifestes SECS lus localement (bundle source + working-tree/
# SoT), zéro MCP, zéro schéma-contrat figé touché.


def _archetype_axes(project_type: str) -> dict[str, list[str]]:
    """Les axes-qualité de l'archétype d'un type de bundle : `{axe: [mots-clés]}`. Vide (⇒ gate no-op honnête)
    si le type ne déclare pas d'`archetype`, si le catalogue `depth-axes.yaml` est absent, ou si l'archétype
    n'y figure pas. Lu depuis le **bundle source** (déterministe, toujours présent), jamais le MCP."""
    try:
        archetype = read_bundle_manifest(project_type).get("archetype")
        raw = load_bundle(project_type).get(_DEPTH_AXES_PATH) if archetype else None
    except BundleError:
        return {}
    if not archetype or not raw:
        return {}
    catalog = yaml.safe_load(raw) or {}
    axes = catalog.get(archetype) or {}
    return {str(name): list(kws or []) for name, kws in axes.items()}


def _roadmap_corpus(conn: sqlite3.Connection, project_slug: str) -> str:
    """Tout le texte de la roadmap (slugs + titres des features & tasks + acceptances) en minuscules — le
    substrat du match mot-clé de couverture. Read-only."""
    parts: list[str] = []
    for f in model.list_features(conn, project_slug):
        parts += [f["slug"], f.get("title") or ""]
        for t in model.list_tasks(conn, f["id"]):
            parts += [t["slug"], t.get("title") or "", t.get("acceptance") or ""]
    return " ".join(parts).lower()


def load_deferred_axes(settings: Settings, project_slug: str, *,
                       cwd: Path | None = None, git: InternalGit | None = None) -> dict[str, str]:
    """Les axes DIFFÉRÉS explicitement par le worker : `{axe: raison}` (raison vide ignorée). Lu là où le
    fichier `.forgemaster/deferred-axes.yaml` existe — d'abord le **working-tree** (`cwd` = worktree du worker
    :
    reflète l'édition non commitée pendant l'interview), sinon la version **commitée** du SoT bare
    (`read_blob HEAD`, déterministe hors worktree, bare-safe). Absent/illisible → `{}` (fail-closed : un axe
    non couvert sans report reste `UNCOVERED_AXIS`)."""
    raw: str | None = None
    if cwd is not None:
        f = Path(cwd) / _DEFERRED_AXES_PATH
        if f.is_file():
            raw = f.read_text(encoding="utf-8")
    if raw is None:
        git = git or InternalGit()
        try:
            raw = git.read_blob(sot_path_for(settings, project_slug), "HEAD", _DEFERRED_AXES_PATH)["content"]
        except (GitOpError, OSError):
            return {}
    parsed = yaml.safe_load(raw) or {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if str(v).strip()}


def check_depth_axes(conn: sqlite3.Connection, project_slug: str, *,
                     deferred: dict[str, str] | None = None) -> list[Issue]:
    """Défauts de PROFONDEUR : pour l'archétype du projet, chaque axe-qualité doit être **couvert** (une
    feature/task le mentionne — un mot-clé de l'axe ⊆ slug/titre/acceptance) ou **différé** (`deferred[axe]`
    non vide). Un axe ni couvert ni différé ⇒ `UNCOVERED_AXIS`. Type sans archétype / catalogue absent ⇒ liste
    vide (no-op). Read-only, déterministe. `deferred` : cf. `load_deferred_axes` (défaut vide)."""
    deferred = deferred or {}
    project = get_project(conn, project_slug)
    axes = _archetype_axes(project["project_type"])
    if not axes:
        return []
    corpus = _roadmap_corpus(conn, project_slug)
    issues: list[Issue] = []
    for axis, keywords in axes.items():
        if any(kw.lower() in corpus for kw in keywords):
            continue
        if deferred.get(axis, "").strip():
            continue
        arche = project["project_type"]
        issues.append(Issue("UNCOVERED_AXIS", "-", axis,
                            f"axe de l'archétype {arche!r} ni couvert ni différé — "
                            f"couvre-le, ou trace-le dans {_DEFERRED_AXES_PATH}"))
    return issues


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `forgemaster roadmap check <project> [--depth]`. Rapport groupé par feature ; **exit 1 dès une
    issue**. `--depth` ajoute le gate de profondeur par archétype et surface les reports."""
    conn = store.open_db(settings)
    deferred: dict[str, str] = {}
    try:
        issues = check_roadmap(conn, args.project)
        if getattr(args, "depth", False):
            deferred = load_deferred_axes(settings, args.project, cwd=Path.cwd())
            issues = issues + check_depth_axes(conn, args.project, deferred=deferred)
        feats = model.list_features(conn, args.project)
        n_feat = len(feats)
        n_task = sum(len(model.list_tasks(conn, f["id"])) for f in feats)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    if deferred:                             # surface les reports tracés (jamais de cap silencieux)
        print(f"axes différés (tracés, {len(deferred)}) : {', '.join(sorted(deferred))}")
    if not issues:
        depth = " + profondeur" if getattr(args, "depth", False) else ""
        print(f"roadmap opérationnelle{depth} : {n_feat} features, {n_task} tasks — 0 issue")
        return 0
    print(f"roadmap NON opérationnelle : {len(issues)} issue(s)")
    for iss in issues:
        loc = f"{iss.feature}/{iss.task}" if iss.task else iss.feature
        print(f"  [{iss.kind}] {loc} — {iss.detail}")
    return 1
