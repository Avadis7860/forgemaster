"""resolver — le résolveur DAG des tasks : classe chaque task (READY/BLOCKED_DEPS/CYCLE/ERROR + états de
statut) et calcule la **NEXT** task dispatchable d'une feature. Le graphe est la **seule autorité de
séquencement** (spec task-next-resolver-dag) ; zéro LLM, déterministe, read-only.

Port : `services/aggregator/lib/vault_tasks.py` (moteur `task-graph-v1` : `detect_cycles`, `classify`,
`_rank_key`) **adapté au modèle cockpit** (tasks d'une feature, `depends_on` = slugs intra-feature).
Refactor **#9** : classification à **ordre figé** (statut > ERROR dangling > CYCLE > BLOCKED_DEPS >
READY) ; **`eff_prio` transitive absorbée** du proto (`task_resolver_proto` : une task qui débloque une
priorité plus haute remonte) ; ordre total avec tiebreak `slug` (zéro ex-æquo). `task add` = saisie
(délègue à `model`).
"""
from __future__ import annotations

import argparse

from cockpit.config import Settings
from cockpit.db import store
from cockpit.roadmap import model

PRIORITIES = ("P0", "P1", "P2", "P3")
PRIO = {p: i for i, p in enumerate(PRIORITIES)}
_UNKNOWN_PRIO = len(PRIORITIES)  # priorité hors vocab → derrière tout le monde (fail-soft)

# Statuts DB (cockpit) → états du résolveur. `todo` seul peut devenir READY/BLOCKED_DEPS.
_TERMINAL_STATE = {"done": "DONE", "cancelled": "CANCELLED", "in_progress": "ACTIVE", "blocked": "BLOCKED"}


def detect_cycles(index: dict[str, dict]) -> set[str]:
    """Membres d'un cycle de dépendances (DFS colorée ; arêtes dangling ignorées). Porté de vault_tasks."""
    color: dict[str, int] = {}   # 0 white / 1 grey / 2 black
    members: set[str] = set()

    def visit(u: str, path: list[str]) -> None:
        color[u] = 1
        path.append(u)
        for v in index[u]["depends_on"]:
            if v not in index:
                continue
            if color.get(v, 0) == 1 and v in path:      # back-edge → cycle
                members.update(path[path.index(v):])
            elif color.get(v, 0) == 0:
                visit(v, path)
        color[u] = 2
        path.pop()

    for t in index:
        if color.get(t, 0) == 0:
            visit(t, [])
    return members


def classify(index: dict[str, dict]) -> dict[str, dict]:
    """Classe chaque task (ordre figé : statut terminal > ERROR dangling > CYCLE > BLOCKED_DEPS > READY).
    Le **statut est la source de vérité** (pas de promotion silencieuse). Retourne {slug: {…, state,
    blockers}}."""
    cyc = detect_cycles(index)
    out: dict[str, dict] = {}
    for sid, t in index.items():
        status, deps = t["status"], t["depends_on"]
        missing = [d for d in deps if d not in index]
        blockers: list[str] = []
        if status in _TERMINAL_STATE:
            state = _TERMINAL_STATE[status]
        elif missing:
            state, blockers = "ERROR", [f"{d} (task inconnue)" for d in missing]
        elif sid in cyc:
            state = "CYCLE"
        else:
            unmet = [d for d in deps if index[d]["status"] != "done"]
            state = "READY" if not unmet else "BLOCKED_DEPS"
            blockers = [f"{d} ({index[d]['status']})" for d in unmet]
        out[sid] = {**t, "state": state, "blockers": blockers}
    return out


def eff_prio(index: dict[str, dict]) -> dict[str, int]:
    """Priorité **effective transitive** : `eff(t) = min(prio propre, min sur dépendants transitifs)`.
    Une task de faible priorité qui débloque une task plus prioritaire remonte (absorbé du proto).
    Robuste aux cycles (garde de pile)."""
    dependents: dict[str, set[str]] = {sid: set() for sid in index}
    for sid, t in index.items():
        for d in t["depends_on"]:
            if d in index:
                dependents[d].add(sid)
    memo: dict[str, int] = {}

    def eff(sid: str, stack: frozenset[str]) -> int:
        if sid in memo:
            return memo[sid]
        best = PRIO.get(index[sid]["priority"], _UNKNOWN_PRIO)
        for child in dependents[sid]:
            if child not in stack:
                best = min(best, eff(child, stack | {sid}))
        memo[sid] = best
        return best

    return {sid: eff(sid, frozenset()) for sid in index}


def _rank_key(t: dict, effp: dict[str, int]) -> tuple:
    """Ordre total : priorité effective ↑, puis date de création ↑, puis slug (tiebreak, zéro ex-æquo)."""
    return (effp[t["slug"]], t.get("created_at") or "9999", t["slug"])


def ready(index: dict[str, dict]) -> list[dict]:
    """Tasks READY d'une feature, triées par ordre total (la tête = la NEXT)."""
    classified = classify(index)
    effp = eff_prio(index)
    return sorted((t for t in classified.values() if t["state"] == "READY"),
                  key=lambda t: _rank_key(t, effp))


def resolve_next(index: dict[str, dict]) -> dict | None:
    """La prochaine task dispatchable (READY de plus haut rang), ou None si aucune."""
    r = ready(index)
    return r[0] if r else None


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
