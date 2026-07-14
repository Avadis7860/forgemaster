"""resolver — adaptateur mince sur le moteur générique `taskmap` (SoT unique du séquencement).

Le DAG des tasks est classé et rangé par `taskmap.core.graph` (`detect_cycles`, `eff_prio`, rang canonique)
+ `taskmap.classify` (décision d'état), dé-forkés du vault. Ce module ne fait QUE l'adaptation à la forme
cockpit : il projette les rows SQLite (`slug`/`created_at`/`status` cockpit) vers la forme de record du cœur
(`id`/`created`/`status` taskmap), délègue, puis **re-traduit** au contrat JSON cockpit (état + blockers en
vocab cockpit). Le graphe reste la **seule autorité de séquencement** ; zéro copie du moteur ici.

Historique : ce fichier était un **fork vendoré** de `task-graph-v1` (port de `vault_tasks.py`, aggregator).
Dé-forké au deep-dive `taskmap-cockpit-backlog-wiring` (P1) — au passage, la priorité effective transitive
`eff_prio` a **gradué dans le cœur taskmap** (distillation-vers-le-centre). `task add`/`next` = saisie /
résolution (délègue à `model` / au cœur).
"""
from __future__ import annotations

import argparse

from taskmap.classify import classify as _tm_classify
from taskmap.core.graph import eff_prio as _core_eff_prio
from taskmap.core.graph import rank_ready as _core_rank_ready

from cockpit.config import Settings
from cockpit.db import store
from cockpit.roadmap import model

PRIORITIES = ("P0", "P1", "P2", "P3")
PRIO = {p: i for i, p in enumerate(PRIORITIES)}
_UNKNOWN_PRIO = len(PRIORITIES)   # priorité hors vocab → dernier (rang de features, orchestrateur)

# Statuts DB cockpit → vocab de statut taskmap. Seul `in_progress`→`active` diverge (todo/done/blocked/
# cancelled communs) ; `todo` tombe dans la branche READY/BLOCKED_DEPS du cœur (non terminal).
_STATUS_TO_TM = {"in_progress": "active"}


def _to_records(index: dict[str, dict]) -> dict[str, dict]:
    """Projette les rows cockpit vers la forme de record du cœur taskmap. Superset (garde `slug`/`created_at`/
    `status` originaux → permet la re-traduction) : `id`≡slug, `created`≡created_at, `tags:[]` (requis par
    `taskmap.classify` ; jamais un épic sur un row cockpit), statut traduit."""
    out: dict[str, dict] = {}
    for slug, row in index.items():
        status = row["status"]
        out[slug] = {**row, "id": slug, "created": row.get("created_at") or "",
                     "status": _STATUS_TO_TM.get(status, status), "tags": []}
    return out


def _blockers(state: str, row: dict, index: dict[str, dict]) -> list[str]:
    """Blockers en **vocab cockpit** (contrat JSON préservé), re-générés depuis l'index ORIGINAL : taskmap
    fournit l'ÉTAT, cockpit garde sa copy UI. ERROR → dep absente « (task inconnue) » ; BLOCKED_DEPS → dep
    non-done avec son statut ORIGINAL ; sinon aucun."""
    deps = row["depends_on"]
    if state == "ERROR":
        return [f"{d} (task inconnue)" for d in deps if d not in index]
    if state == "BLOCKED_DEPS":
        return [f"{d} ({index[d]['status']})" for d in deps if index[d]["status"] != "done"]
    return []


def _classify_tm(index: dict[str, dict]) -> dict[str, dict]:
    """Classe via le moteur taskmap (`root`/`today` None : ni trigger ni épic sur des rows cockpit → états =
    sous-ensemble DONE/CANCELLED/ACTIVE/BLOCKED/ERROR/CYCLE/READY/BLOCKED_DEPS)."""
    return _tm_classify(_to_records(index), None, None)


def classify(index: dict[str, dict]) -> dict[str, dict]:
    """Classe chaque task — **état = autorité taskmap** — et rend le contrat cockpit `{**row, state,
    blockers}` byte-identique (blockers re-traduits en vocab cockpit)."""
    tm = _classify_tm(index)
    out: dict[str, dict] = {}
    for slug, row in index.items():
        state = tm[slug]["state"]
        out[slug] = {**row, "state": state, "blockers": _blockers(state, row, index)}
    return out


def eff_prio(index: dict[str, dict]) -> dict[str, int]:
    """Priorité effective transitive (déléguée au cœur taskmap). Dict keyé par slug (record `id`≡slug)."""
    return _core_eff_prio(_to_records(index), PRIO)


def resolve_next(index: dict[str, dict]) -> dict | None:
    """La prochaine task dispatchable (READY de plus haut rang canonique), re-traduite au contrat cockpit ;
    None si aucune READY. Rang délégué au cœur (`rank_ready` : `eff_prio` transitive + tiebreaks)."""
    ranked = _core_rank_ready(_classify_tm(index), PRIO)
    if not ranked:
        return None
    row = index[ranked[0]["id"]]              # ranked[0]["id"] ≡ slug
    return {**row, "state": "READY", "blockers": []}


def index_for_feature(conn, feature_ref: str) -> dict[str, dict]:
    """Construit l'index {slug: task} d'une feature depuis la DB."""
    feature = model.resolve_feature(conn, feature_ref)
    return {t["slug"]: t for t in model.list_tasks(conn, feature["id"])}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit task <action>` (add|next). `add` = saisie (data layer) ; `next` = résolveur DAG."""
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
