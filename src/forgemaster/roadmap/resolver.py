"""resolver — adaptateur mince sur le moteur générique `taskmap` (SoT unique du séquencement).

Le DAG des tasks est classé et rangé par `taskmap.graph` (`detect_cycles`, `eff_prio`, rang canonique)
+ `taskmap.classify` (décision d'état), dé-forkés du vault. `taskmap.graph` est l'**adresse publique** du
moteur — `taskmap.core.*` en est l'implémentation interne, et un consommateur n'y descend pas : c'est le
socle, il doit rester libre de bouger. Ce module ne fait QUE l'adaptation à la forme
forgemaster : il projette les rows SQLite (`slug`/`created_at`/`status` forgemaster) vers la forme de record
du cœur
(`id`/`created`/`status` taskmap), délègue, puis **re-traduit** au contrat JSON forgemaster (état + blockers
en
vocab forgemaster). Le graphe reste la **seule autorité de séquencement** ; zéro copie du moteur ici.

Historique : ce fichier était un **fork vendoré** de `task-graph-v1` (port de `vault_tasks.py`, aggregator).
Dé-forké au deep-dive `taskmap-cockpit-backlog-wiring` (P1) — au passage, la priorité effective transitive
`eff_prio` a **gradué dans le cœur taskmap** (distillation-vers-le-centre). `task add`/`next` = saisie /
résolution (délègue à `model` / au cœur).
"""
from __future__ import annotations

import argparse

from taskmap.classify import classify as _tm_classify
from taskmap.graph import eff_prio as _tm_eff_prio
from taskmap.graph import rank_ready as _tm_rank_ready

from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.roadmap import model

PRIORITIES = ("P0", "P1", "P2", "P3")
PRIO = {p: i for i, p in enumerate(PRIORITIES)}
_UNKNOWN_PRIO = len(PRIORITIES)   # priorité hors vocab → dernier (rang de features, orchestrateur)

# Statuts DB forgemaster → vocab de statut taskmap. Seul `in_progress`→`active` diverge (todo/done/blocked/
# cancelled communs) ; `todo` tombe dans la branche READY/BLOCKED_DEPS du cœur (non terminal).
_STATUS_TO_TM = {"in_progress": "active"}


def _to_records(index: dict[str, dict]) -> dict[str, dict]:
    """Projette les rows forgemaster vers la forme de record du cœur taskmap. Superset (garde
    `slug`/`created_at`/
    `status` originaux → permet la re-traduction) : `id`≡slug, `created`≡created_at, `tags:[]` (requis par
    `taskmap.classify` ; jamais un épic sur un row forgemaster), statut traduit."""
    out: dict[str, dict] = {}
    for slug, row in index.items():
        status = row["status"]
        out[slug] = {**row, "id": slug, "created": row.get("created_at") or "",
                     "status": _STATUS_TO_TM.get(status, status), "tags": []}
    return out


def _blockers(state: str, row: dict, index: dict[str, dict]) -> list[str]:
    """Blockers en **vocab forgemaster** (contrat JSON préservé), re-générés depuis l'index ORIGINAL : taskmap
    fournit l'ÉTAT, forgemaster garde sa copy UI. ERROR → dep absente « (task inconnue) » ; BLOCKED_DEPS → dep
    non-done avec son statut ORIGINAL ; sinon aucun."""
    deps = row["depends_on"]
    if state == "ERROR":
        return [f"{d} (task inconnue)" for d in deps if d not in index]
    if state == "BLOCKED_DEPS":
        return [f"{d} ({index[d]['status']})" for d in deps if index[d]["status"] != "done"]
    return []


def _classify_tm(index: dict[str, dict]) -> dict[str, dict]:
    """Classe via le moteur taskmap (`root`/`today` None : ni trigger ni épic sur des rows forgemaster → états
    =
    sous-ensemble DONE/CANCELLED/ACTIVE/BLOCKED/ERROR/CYCLE/READY/BLOCKED_DEPS)."""
    return _tm_classify(_to_records(index), None, None)


def classify(index: dict[str, dict]) -> dict[str, dict]:
    """Classe chaque task — **état = autorité taskmap** — et rend le contrat forgemaster `{**row, state,
    blockers}` byte-identique (blockers re-traduits en vocab forgemaster)."""
    tm = _classify_tm(index)
    out: dict[str, dict] = {}
    for slug, row in index.items():
        state = tm[slug]["state"]
        out[slug] = {**row, "state": state, "blockers": _blockers(state, row, index)}
    return out


def eff_prio(index: dict[str, dict]) -> dict[str, int]:
    """Priorité effective transitive (déléguée au cœur taskmap). Dict keyé par slug (record `id`≡slug)."""
    return _tm_eff_prio(_to_records(index), PRIO)


def resolve_next(index: dict[str, dict]) -> dict | None:
    """La prochaine task dispatchable (READY de plus haut rang canonique), re-traduite au contrat forgemaster
    ;
    None si aucune READY. Rang délégué au cœur (`rank_ready` : `eff_prio` transitive + tiebreaks)."""
    ranked = _tm_rank_ready(_classify_tm(index), PRIO)
    if not ranked:
        return None
    row = index[ranked[0]["id"]]              # ranked[0]["id"] ≡ slug
    return {**row, "state": "READY", "blockers": []}


def index_for_feature(conn, feature_ref: str) -> dict[str, dict]:
    """Construit l'index {slug: task} d'une feature depuis la DB."""
    feature = model.resolve_feature(conn, feature_ref)
    return {t["slug"]: t for t in model.list_tasks(conn, feature["id"])}


# --- DAG INTER-feature (v10) : même moteur taskmap, une couche AU-DESSUS des tasks ------------------------
# Statuts DB feature → vocab taskmap. Le terminal-**satisfait** d'une FEATURE = `merged` (≡ `done` taskmap) :
# une feature prérequise n'est « faite » qu'une fois mergée. Les autres passent (`planned`/`ready` → non
# terminal donc non-satisfaisant ; `active` → ACTIVE ; `cancelled` → terminal mais ≠ done → un dépendant reste
# BLOCKED_DEPS = deadlock, surfacé par `check` en DEAD_FEATURE_DEP). NB : l'index feature est projet-global,
# jamais threadé dans l'index task (invariant « 1 index taskmap = 1 feature » du séquencement intra préservé).
_FEATURE_STATUS_TO_TM = {"merged": "done"}


def _feature_records(features: list[dict]) -> dict[str, dict]:
    """Projette les rows feature vers la forme de record taskmap (id≡slug, statut mappé, tags:[])."""
    out: dict[str, dict] = {}
    for f in features:
        status = f["status"]
        out[f["slug"]] = {**f, "id": f["slug"], "created": f.get("created_at") or "",
                          "status": _FEATURE_STATUS_TO_TM.get(status, status),
                          "depends_on": f["depends_on"], "tags": []}
    return out


def _feature_blockers(state: str, feature: dict, records: dict[str, dict]) -> list[str]:
    """Blockers inter-feature en vocab forgemaster. ERROR → prérequis absent « (feature inconnue) » ;
    BLOCKED_DEPS → prérequis non-mergé avec son statut (le mappé ≡ l'original hors `merged`, filtré ici)."""
    deps = feature["depends_on"]
    if state == "ERROR":
        return [f"{d} (feature inconnue)" for d in deps if d not in records]
    if state == "BLOCKED_DEPS":
        return [f"{d} ({records[d]['status']})" for d in deps if records[d]["status"] != "done"]
    return []


def classify_features(conn, project: str) -> dict[str, dict]:
    """Classe le DAG **inter-feature** d'un projet — même autorité taskmap que le DAG des tasks, une couche
    au-dessus. Une feature est READY quand toutes ses prérequises sont `merged` ; BLOCKED_DEPS sinon ;
    ERROR (prérequis inconnu) / CYCLE comme le DAG des tasks. Retour : `{slug: {**feature, state, blockers}}`.
    """
    features = model.list_features(conn, project)
    records = _feature_records(features)
    tm = _tm_classify(records, None, None)
    out: dict[str, dict] = {}
    for f in features:
        state = tm[f["slug"]]["state"]
        out[f["slug"]] = {**f, "state": state, "blockers": _feature_blockers(state, f, records)}
    return out


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `forgemaster task <action>` (add|set-deps|next). `add`/`set-deps` = saisie/édition (data layer) ;
    `next` = résolveur DAG."""
    conn = store.open_db(settings)
    try:
        if args.action == "add":
            acceptance = getattr(args, "acceptance", None)
            if not (acceptance or "").strip():
                print("erreur : --acceptance ne peut pas être vide (task sans DoD = non dispatchable)")
                return 1
            t = model.add_task(conn, feature_ref=args.feature, slug=args.slug, title=args.title,
                               depends_on=args.depends_on, priority=getattr(args, "priority", "P1"),
                               acceptance=acceptance)
            print(f"task créée : {args.feature}/{t['slug']} (priorité {t['priority']})")
            return 0
        if args.action == "set-deps":
            t = model.set_task_deps(conn, feature_ref=args.feature, slug=args.slug,
                                    depends_on=getattr(args, "depends_on", None) or [])
            deps = ", ".join(t["depends_on"]) or "(aucune)"
            print(f"deps de {args.feature}/{t['slug']} mises à jour : {deps}")
            return 0
        # action == "next"
        index = index_for_feature(conn, args.feature)
        if not index:
            print("aucune task dans cette feature")
            return 0
        nxt = resolve_next(index)
        if nxt is None:
            classified = classify(index)
            counts = _counts(classified)
            print(f"aucune task READY — {counts}")
            return 0
        print(f"NEXT : {nxt['slug']} — {nxt['title']} (priorité {nxt['priority']})")
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    return 0


def _counts(classified: dict[str, dict]) -> str:
    tally: dict[str, int] = {}
    for t in classified.values():
        tally[t["state"]] = tally.get(t["state"], 0) + 1
    return ", ".join(f"{k}:{v}" for k, v in sorted(tally.items()))
