"""model — CRUD de la roadmap (features + tasks) sur la DB, et (dé)sérialisation du contrat
`.cockpit/roadmap.yaml` (schéma figé — cf. docs/schema-contract.md § roadmap.yaml).

Une **feature** = branche = worktree (l'unité de merge) ; une **task** = unité de dispatch séquentielle,
`depends_on` explicite (JSON). Référence de feature = `"<projet>/<feature>"` (sans ambiguïté inter-projets).

Port : concept du contrat data v2 des tasks vault. Refactor #9 (la donnée porte `depends_on` ; le
séquencement est dérivé par `resolver`, pas ici).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime

import yaml

from cockpit.config import Settings
from cockpit.core import ids
from cockpit.db import store
from cockpit.projects.registry import get_project
from cockpit.provision import BundleError, read_bundle_manifest

ROADMAP_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _project_facets(project: dict) -> set[str]:
    """Vocabulaire de facettes **valide pour CE projet** = les facettes de son bundle **∪ les facettes de
    base** (registre filesystem, keyed par `project_type`) — registre-driven, jamais un enum global
    (`bundle_registry_source`). Les facettes de base (doc + cross-cutting code/test/infra/review) sont
    héritées par TOUT type (composition `base ⊕ overlay`) → toujours dispatchables, même si un overlay ne
    les re-déclare pas. Type retiré/cassé → fallback `{'doc'}` (base, toujours présente). Fail-soft."""
    own: set[str] = set()
    try:
        pt = project.get("project_type") or "generic"
        own = set(read_bundle_manifest(pt).get("facets") or [])
    except BundleError:
        pass
    try:
        base = set(read_bundle_manifest("generic").get("facets") or [])
    except BundleError:
        base = {"doc"}
    return (own | base) or {"doc"}


def add_feature(conn: sqlite3.Connection, *, project_slug: str, slug: str, title: str | None = None,
                facet: str | None = None, blueprint: str | None = None,
                depends_on: list[str] | None = None) -> dict:
    """Ajoute une feature à un projet (branche `feature/<slug>`, statut `planned`). `facet` (optionnel) =
    la facette de dispatch qui alignera le worker ; NULL → défaut résolu du `bundle.toml` au dispatch.
    `facet` est validée contre les facettes **du bundle du projet** (registre), pas un vocab global.
    `blueprint` (optionnel, v9) = ref STAMP (id d'un blueprint central), résolue au read du board.
    `depends_on` (optionnel, v10) = slugs de features prérequises (DAG **inter**-feature ; une feature reste
    non-dispatchable tant qu'une prérequise n'est pas `merged`). Le row RENDU décode `depends_on` en liste."""
    ids.ensure_slug(slug, field="feature")
    project = get_project(conn, project_slug)
    valid = _project_facets(project)
    if facet is not None and facet not in valid:
        raise ValueError(f"facette {facet!r} hors vocab du bundle {project['project_type']!r} "
                         f"du projet {project_slug} : {sorted(valid)}")
    deps = list(depends_on or [])
    row = {"id": ids.new_id(), "project_id": project["id"], "slug": slug, "title": title or slug,
           "branch": f"feature/{slug}", "worktree_path": None, "status": "planned", "facet": facet,
           "blueprint": blueprint, "depends_on": json.dumps(deps), "created_at": _now()}
    try:
        conn.execute(
            "INSERT INTO features (id, project_id, slug, title, branch, worktree_path, status, facet, "
            "blueprint, depends_on, created_at) VALUES (:id, :project_id, :slug, :title, :branch, "
            ":worktree_path, :status, :facet, :blueprint, :depends_on, :created_at)", row)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"feature déjà existante : {project_slug}/{slug}") from exc
    return {**row, "depends_on": deps}


def resolve_feature(conn: sqlite3.Connection, ref: str) -> dict:
    """Résout une référence `"<projet>/<feature>"` → row feature. Lève `KeyError`/`ValueError` sinon."""
    if "/" not in ref:
        raise ValueError(f"référence feature attendue « projet/feature » : {ref!r}")
    project_slug, feature_slug = ref.split("/", 1)
    project = get_project(conn, project_slug)
    r = conn.execute("SELECT * FROM features WHERE project_id = ? AND slug = ?",
                     (project["id"], feature_slug)).fetchone()
    if r is None:
        raise KeyError(ref)
    d = dict(r)
    d["depends_on"] = json.loads(d["depends_on"])   # v10 : DAG inter-feature décodé
    return d


def set_feature_deps(conn: sqlite3.Connection, *, ref: str, depends_on: list[str]) -> dict:
    """Édite le DAG **inter-feature** d'une feature déjà créée : REMPLACE ses `depends_on` (v10). Là où
    `add_feature` reste souple (forward-ref au build en lot), l'édition valide l'arête ciblée — le graphe
    existe déjà. Validation = **réutilise l'autorité `resolver.classify_features`** (dangling→ERROR,
    cycle/self-dep→CYCLE), jamais un 2ᵉ détecteur. Écriture scopée `WHERE id=?` (la clé résolue tue le footgun
    « ambiguous column name: slug »). Write-validate-rollback en une transaction ; le row RENDU décode
    `depends_on`. Lève `KeyError` (feature inconnue), `ValueError` (dep pendante / cycle)."""
    from cockpit.roadmap import resolver  # import paresseux : resolver importe déjà model (cycle)
    feature = resolve_feature(conn, ref)              # KeyError si absente
    project_slug = ref.split("/", 1)[0]
    deps = list(depends_on)
    conn.execute("UPDATE features SET depends_on = ? WHERE id = ?",
                 (json.dumps(deps), feature["id"]))   # PAS de commit : validé sur l'écriture non-commitée
    node = resolver.classify_features(conn, project_slug)[feature["slug"]]
    if node["state"] == "ERROR":
        conn.rollback()
        raise ValueError(f"feature prérequise inexistante : {', '.join(node['blockers'])}")
    if node["state"] == "CYCLE":
        conn.rollback()
        raise ValueError(f"cycle de dépendances inter-features introduit par {feature['slug']}")
    conn.commit()
    return {**feature, "depends_on": deps}


TASK_MODES = ("headless", "interactive")


def add_task(conn: sqlite3.Connection, *, feature_ref: str, slug: str, title: str | None = None,
             depends_on: list[str] | None = None, priority: str = "P1",
             acceptance: str | None = None, mode: str = "headless") -> dict:
    """Ajoute une task à une feature. `depends_on` = slugs de tasks prérequises (dans la même feature).
    `acceptance` (optionnel, TEXT libre) = critères de DoD injectés dans le prompt du worker au dispatch.
    `mode` (v12, `headless`|`interactive`, défaut `headless`) = identité de dispatch : une task `interactive`
    est routée vers un terminal interactif (`cockpit interview`) au lieu d'un worker `claude -p` headless."""
    ids.ensure_slug(slug, field="task")
    if priority not in ("P0", "P1", "P2", "P3"):
        raise ValueError(f"priorité hors vocab P0-P3 : {priority!r}")
    if mode not in TASK_MODES:
        raise ValueError(f"mode hors vocab {TASK_MODES} : {mode!r}")
    feature = resolve_feature(conn, feature_ref)
    row = {"id": ids.new_id(), "feature_id": feature["id"], "slug": slug, "title": title or slug,
           "status": "todo", "depends_on": json.dumps(depends_on or []), "priority": priority,
           "acceptance": acceptance, "mode": mode, "created_at": _now()}
    try:
        conn.execute(
            "INSERT INTO tasks (id, feature_id, slug, title, status, depends_on, priority, acceptance, "
            "mode, created_at) VALUES (:id, :feature_id, :slug, :title, :status, :depends_on, :priority, "
            ":acceptance, :mode, :created_at)", row)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"task déjà existante : {feature_ref}/{slug}") from exc
    return row


def set_task_deps(conn: sqlite3.Connection, *, feature_ref: str, slug: str, depends_on: list[str]) -> dict:
    """Édite le DAG **intra-feature** d'une task déjà créée : REMPLACE ses `depends_on` (slugs de tasks de la
    même feature). Symétrique de `set_feature_deps` : validation via **l'autorité `resolver.classify`**
    (dangling→ERROR, cycle/self-dep→CYCLE), écriture scopée `WHERE feature_id=? AND slug=?` (jamais un
    `WHERE slug=?` ambigu). Write-validate-rollback ; le row RENDU décode `depends_on`. Lève `KeyError`
    (feature/task inconnue), `ValueError` (dep pendante / cycle)."""
    from cockpit.roadmap import resolver  # import paresseux (cf. set_feature_deps)
    feature = resolve_feature(conn, feature_ref)      # KeyError si feature absente
    exists = conn.execute("SELECT 1 FROM tasks WHERE feature_id = ? AND slug = ?",
                          (feature["id"], slug)).fetchone()
    if exists is None:
        raise KeyError(f"{feature_ref}/{slug}")
    deps = list(depends_on)
    conn.execute("UPDATE tasks SET depends_on = ? WHERE feature_id = ? AND slug = ?",
                 (json.dumps(deps), feature["id"], slug))     # scopé par feature_id : pas d'ambiguïté
    index = {t["slug"]: t for t in list_tasks(conn, feature["id"])}
    classified = resolver.classify(index)             # voit l'écriture non-commitée (même conn)
    state = classified[slug]["state"]
    if state == "ERROR":
        conn.rollback()
        raise ValueError(f"dépendance inexistante : {', '.join(classified[slug]['blockers'])}")
    if state == "CYCLE":
        conn.rollback()
        raise ValueError(f"cycle de dépendances introduit par {slug}")
    conn.commit()
    return {**dict(index[slug]), "depends_on": deps}


def list_features(conn: sqlite3.Connection, project_slug: str) -> list[dict]:
    project = get_project(conn, project_slug)
    out = []
    for r in conn.execute("SELECT * FROM features WHERE project_id = ? ORDER BY slug", (project["id"],)):
        d = dict(r)
        d["depends_on"] = json.loads(d["depends_on"])   # v10 : DAG inter-feature décodé (cf. list_tasks)
        out.append(d)
    return out


def list_tasks(conn: sqlite3.Connection, feature_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM tasks WHERE feature_id = ? ORDER BY slug", (feature_id,))
    out = []
    for r in rows:
        d = dict(r)
        d["depends_on"] = json.loads(d["depends_on"])
        out.append(d)
    return out


def _feature_doc(f: dict) -> dict:
    """Un bloc feature du contrat roadmap.yaml. `facet`/`acceptance` (v6) et `blueprint` (v9) émis SEULEMENT
    si présents (contrat rétro-compatible : une roadmap sans ces champs reste identique à la v1)."""
    doc: dict = {"slug": f["slug"], "title": f["title"]}
    if f.get("facet"):
        doc["facet"] = f["facet"]
    if f.get("blueprint"):
        doc["blueprint"] = f["blueprint"]     # ref STAMP brute (v9) ; la résolution est runtime/board-only
    if f.get("depends_on"):
        doc["depends_on"] = f["depends_on"]   # DAG inter-feature (v10) — émis seulement si non-vide
    doc["tasks"] = []
    for t in f.get("tasks", []):
        task: dict = {"slug": t["slug"], "title": t["title"], "priority": t["priority"],
                      "depends_on": t["depends_on"]}
        if t.get("acceptance"):
            task["acceptance"] = t["acceptance"]
        if t.get("mode") and t["mode"] != "headless":
            task["mode"] = t["mode"]              # émis seulement si interactif (contrat rétro-compat v1)
        doc["tasks"].append(task)
    return doc


def to_yaml(project_slug: str, features: list[dict]) -> str:
    """Sérialise une roadmap (features + leurs tasks) au contrat `.cockpit/roadmap.yaml`. PUR."""
    doc = {"version": ROADMAP_VERSION, "project": project_slug,
           "features": [_feature_doc(f) for f in features]}
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit roadmap <action>` (add-feature|set-deps|show)."""
    conn = store.open_db(settings)
    try:
        if args.action == "add-feature":
            f = add_feature(conn, project_slug=args.project, slug=args.slug, title=args.title,
                            facet=getattr(args, "facet", None),
                            depends_on=getattr(args, "depends_on", None) or [])
            fac = f" [{f['facet']}]" if f.get("facet") else ""
            print(f"feature créée : {args.project}/{f['slug']}{fac} — branche {f['branch']}")
        elif args.action == "set-deps":
            f = set_feature_deps(conn, ref=f"{args.project}/{args.feature}",
                                 depends_on=getattr(args, "depends_on", None) or [])
            deps = ", ".join(f["depends_on"]) or "(aucune)"
            print(f"deps de {args.project}/{f['slug']} mises à jour : {deps}")
        elif args.action == "show":
            features = list_features(conn, args.project)
            for f in features:
                f["tasks"] = list_tasks(conn, f["id"])
            print(to_yaml(args.project, features))
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    return 0
