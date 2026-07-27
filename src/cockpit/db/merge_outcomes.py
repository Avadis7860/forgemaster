"""merge_outcomes — rend la fiabilité du gate vert MESURABLE (table `merge_outcomes`, v18).

Enabler du checkpoint C0 de l'auto-merge : le C0 a tranché NO-GO car la fiabilité du gate vert est
**inmesurable par conception** (`gate_verdicts` journalise par-SHA, sans issue aval, borné à 20 et le merge
détruit la branche). Ce module crée le **substrat survivant** : chaque merge VERT enregistre une ligne durable
(`record_merge`, best-effort au chokepoint du merge) — le dénominateur « mergé vert au SHA X à l'instant T ».

**Attribution HUMAINE.** Le terrain n'a AUCUN signal automatique d'issue aval : `revert` n'existe pas ;
`refix` ne tourne que sur un gate rouge d'une feature VIVANTE (branche détruite au merge) ; le `merge_sha`
stocké est en fait le `head_sha`, pas le commit de merge. Donc l'`outcome` (`held|reverted|refixed`) est
**marqué par un humain** (`mark_outcome`). Une pré-suggestion advisory est dérivée AU READ (un job après
`merged_at` → rework probable), pour NUDGER la marque — **jamais comptée** dans le taux (numérateur = marques
confirmées seulement).

**Survie au merge.** Table dédiée, hors du journal borné `gate_verdicts` : la ligne persiste après
`delete_branch`/`prune_verdicts` (l'issue aval arrive *après* le merge).

**Best-effort** (patron `db/alerts.py`, `gate/history.py`) : `record_merge` ne fait JAMAIS échouer un merge.
Exception : `mark_outcome` est une MUTATION utilisateur (route API / CLI) → lève `KeyError` sur un merge
inconnu (mappé 404), pas de silence.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cockpit.core import ids
from cockpit.db import store

if TYPE_CHECKING:
    from cockpit.config import Settings

_LOG = logging.getLogger("cockpit")

_OUTCOMES = ("held", "reverted", "refixed")
_ADVERSE = ("reverted", "refixed")

# Colonnes de la table (lecture / vue).
_COLS = ("id", "project", "feature", "feature_ref", "sha", "human_go", "outcome", "note",
         "merged_at", "updated_at", "marked_at")


def _now_iso() -> str:
    """Horodatage ISO-UTC — injectable en test (les timestamps sont des arguments, jamais rejoués)."""
    return datetime.now(UTC).isoformat()


def record_merge(conn: sqlite3.Connection, *, project: str, feature: str, feature_ref: str, sha: str,
                 human_go: bool = True, merged_at: str | None = None) -> None:
    """Enregistre un merge VERT comme dénominateur de fiabilité (outcome initial `held`). `INSERT OR IGNORE`
    sur `ux_merge_outcome(project, feature, sha)` → un merge rejoué au même SHA ne double pas le compte.
    **Best-effort** : ne fait JAMAIS échouer le merge. `merged_at` injecté (défaut = maintenant)."""
    ts = merged_at or _now_iso()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO merge_outcomes (id, project, feature, feature_ref, sha, human_go, "
            "outcome, note, merged_at, updated_at, marked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'held', NULL, ?, ?, NULL)",
            (ids.new_id(), project, feature, feature_ref, sha, 1 if human_go else 0, ts, ts))
        conn.commit()
    except sqlite3.Error as exc:                            # best-effort : une trace ratée ne casse rien
        _LOG.warning("merge_outcome non enregistré (%s @ %s) : %s", feature_ref, sha, exc)


def mark_outcome(conn: sqlite3.Connection, *, project: str, feature: str, outcome: str,
                 sha: str | None = None, note: str | None = None, marked_at: str | None = None) -> dict:
    """Marque l'issue aval d'un merge vert (`held → reverted|refixed`, ou retour à `held`). Cible la ligne du
    `sha` donné, sinon le merge le plus RÉCENT de `(project, feature)`. Mutation utilisateur → lève `KeyError`
    si aucun merge ne correspond (mappé 404). `marked_at` = maintenant si adverse, NULL si retour `held`."""
    if outcome not in _OUTCOMES:
        raise ValueError(f"outcome invalide : {outcome!r}")
    sel = ("SELECT id FROM merge_outcomes WHERE project = ? AND feature = ?"
           + (" AND sha = ?" if sha else "")
           + " ORDER BY merged_at DESC, id DESC LIMIT 1")
    params: list[object] = [project, feature]
    if sha:
        params.append(sha)
    row = conn.execute(sel, params).fetchone()
    if row is None:
        raise KeyError(f"{project}/{feature}" + (f"@{sha}" if sha else ""))
    ts = marked_at or _now_iso()
    stamp = ts if outcome in _ADVERSE else None
    conn.execute(
        "UPDATE merge_outcomes SET outcome = ?, note = ?, marked_at = ?, updated_at = ? WHERE id = ?",
        (outcome, note, stamp, ts, row["id"]))
    conn.commit()
    updated = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM merge_outcomes WHERE id = ?", (row["id"],)).fetchone()
    return _row_view(conn, updated)


def _suggested(conn: sqlite3.Connection, project: str, feature: str, merged_at: str) -> str | None:
    """Pré-suggestion advisory : un job de dispatch de la feature APRÈS son merge → rework probable
    (`refixed`). Dérivé au read, jamais stocké ni compté dans le taux — un simple nudge vers la marque."""
    hit = conn.execute(
        "SELECT 1 FROM dispatch_jobs j JOIN tasks t ON j.task_id = t.id "
        "JOIN features f ON t.feature_id = f.id JOIN projects p ON f.project_id = p.id "
        "WHERE p.slug = ? AND f.slug = ? AND j.started_at > ? LIMIT 1",
        (project, feature, merged_at)).fetchone()
    return "refixed" if hit is not None else None


def _row_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Ligne `merge_outcomes` → vue enrichie (`human_go` bool, `suggested` dérivé). None-safe."""
    out = {c: row[c] for c in _COLS}
    out["human_go"] = bool(out["human_go"])
    out["suggested"] = (_suggested(conn, out["project"], out["feature"], out["merged_at"])
                        if out["outcome"] == "held" else None)
    return out


def list_outcomes(conn: sqlite3.Connection, project: str | None = None) -> list[dict]:
    """Merges verts (plus récent d'abord), enrichis de la pré-suggestion. Lecture PURE. `project=None` =
    tous projets."""
    cols = ", ".join(_COLS)
    if project is None:
        rows = conn.execute(
            f"SELECT {cols} FROM merge_outcomes ORDER BY merged_at DESC, id DESC").fetchall()
    else:
        rows = conn.execute(
            f"SELECT {cols} FROM merge_outcomes WHERE project = ? ORDER BY merged_at DESC, id DESC",
            (project,)).fetchall()
    return [_row_view(conn, r) for r in rows]


def _tally(entries: list[dict]) -> dict:
    """Compte + taux de FIABILITÉ sur une liste de vues. `taux = (n_verts - n_adverse) / n_verts`, `None` si
    n_verts=0 (honnête-vide, jamais 0/0). `n_adverse` = marques confirmées (reverted+refixed), pas suggest.

    **Honnêteté du signal** : dans ce schéma seule une marque ADVERSE laisse une trace durable (`marked_at`) ;
    un `held` est indistinguable d'un merge NON JUGÉ (`n_held` = non jugés). Donc `taux` est une **borne
    optimiste** (suppose tout `held` = bon). `provisional=True` quand `n_adverse=0` : le `taux` vaut alors
    100 % *uniquement* parce que rien n'a été marqué mauvais — jamais à lire comme « santé verte prouvée »."""
    n = len(entries)
    n_reverted = sum(1 for e in entries if e["outcome"] == "reverted")
    n_refixed = sum(1 for e in entries if e["outcome"] == "refixed")
    n_adverse = n_reverted + n_refixed
    return {"n_merges_verts": n, "n_reverted": n_reverted, "n_refixed": n_refixed,
            "n_adverse": n_adverse, "n_held": n - n_adverse, "n_marked": n_adverse,
            "provisional": n > 0 and n_adverse == 0,
            "taux": None if n == 0 else round((n - n_adverse) / n, 4)}


def _open_blockers(conn: sqlite3.Connection, project: str) -> list[dict]:
    """Features 🔴-bloquées d'un projet (`alerts.kind='gate_red'`, `status='open'`), lues pour TEMPÉRER la
    fiabilité au read : une feature bloquée n'entre jamais dans `merge_outcomes` (jamais mergée) → hors taux.
    On la remonte à part pour qu'un `100%` ne passe pas « vert-santé » sur un projet qui bloque.
    Best-effort : la table `alerts` peut manquer (schéma partiel) → liste vide, jamais d'échec."""
    try:
        rows = conn.execute(
            "SELECT feature_ref, feature, reason FROM alerts "
            "WHERE project = ? AND kind = 'gate_red' AND status = 'open' "
            "ORDER BY created_at DESC, id DESC", (project,)).fetchall()
    except sqlite3.Error:
        return []
    return [{"feature_ref": r["feature_ref"], "feature": r["feature"], "reason": r["reason"]} for r in rows]


def reliability(conn: sqlite3.Connection, project: str | None = None) -> dict:
    """Taux de fiabilité du gate vert. `project` fourni → `{scope:'project', project, …tally…, features:[…]}`
    (`KeyError` si projet inconnu). `project=None` → `{scope:'global', …tally…, projects:[{project,…}]}`.
    Fold Python (house style, pas de `GROUP BY` SQL). Un scope sans merge vert → tally à 0 + `taux:None`."""
    if project is not None:
        if conn.execute("SELECT 1 FROM projects WHERE slug = ?", (project,)).fetchone() is None:
            raise KeyError(project)
        entries = list_outcomes(conn, project)
        blocked = _open_blockers(conn, project)
        return {"scope": "project", "project": project, **_tally(entries),
                "n_blocked_open": len(blocked), "blocked_features": blocked, "features": entries}
    entries = list_outcomes(conn, None)
    by_project: dict[str, list[dict]] = {}
    order: list[str] = []
    for e in entries:
        if e["project"] not in by_project:
            by_project[e["project"]] = []
            order.append(e["project"])
        by_project[e["project"]].append(e)
    projects = [{"project": p, **_tally(by_project[p]),
                 "n_blocked_open": len(_open_blockers(conn, p))} for p in order]
    return {"scope": "global", **_tally(entries),
            "n_blocked_open": sum(p["n_blocked_open"] for p in projects), "projects": projects}


# -- surface CLI ------------------------------------------------------------------------------------

def _fmt_taux(taux: float | None) -> str:
    """Taux de fiabilité en % ; `None` (aucun merge vert) = « — »."""
    return "—" if taux is None else f"{taux * 100:.0f}%"


def _print_reliability(data: dict) -> None:
    head = data["project"] if data["scope"] == "project" else "global"
    qual = " ⚠ provisoire (0 marque — borne optimiste)" if data.get("provisional") else ""
    print(f"{head} — fiabilité {_fmt_taux(data['taux'])} "
          f"({data['n_merges_verts']} merges verts · {data['n_adverse']} adverse · "
          f"{data['n_held']} non jugés){qual}")
    blocked = data.get("blocked_features") or []
    if data.get("n_blocked_open"):
        print(f"  ⛔ {data['n_blocked_open']} feature(s) 🔴-bloquée(s) — HORS taux "
              f"(jamais mergée{'s' if data['n_blocked_open'] > 1 else ''}) :")
        for b in blocked:
            print(f"     {b['feature']:<26} {b['reason']}")
    if data["n_merges_verts"] == 0:
        print("  (aucun merge vert encore — lance un drain)")
        return
    if data["scope"] == "global":
        for p in data["projects"]:
            print(f"  {p['project']:<28} {_fmt_taux(p['taux'])} "
                  f"({p['n_merges_verts']} verts · {p['n_adverse']} adverse)")
        return
    for f in data["features"]:
        if f["outcome"] != "held":
            badge = f["outcome"]
        elif f["suggested"]:
            badge = f"held·{f['suggested']}?"                # nudge : rework détecté après le merge
        else:
            badge = "held"
        print(f"  {f['feature']:<28} {badge:<16} {f['sha'][:10]}")


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit reliability show [projet] [--json]` (projet omis = global) / `reliability mark <projet>
    <feature> --outcome {reverted,refixed} [--note …] [--sha …]`."""
    conn = store.open_db(settings)
    try:
        if args.action == "mark":
            try:
                row = mark_outcome(conn, project=args.project, feature=args.feature,
                                   outcome=args.outcome, sha=getattr(args, "sha", None),
                                   note=getattr(args, "note", None))
            except KeyError:
                print(f"erreur : merge introuvable : {args.project}/{args.feature}")
                return 2
            print(json.dumps(row, ensure_ascii=False, indent=2) if getattr(args, "json", False)
                  else f"marqué {row['feature']} → {row['outcome']}")
            return 0
        try:
            data = reliability(conn, getattr(args, "project", None))
        except KeyError:
            print(f"erreur : projet introuvable : {args.project}")
            return 2
    finally:
        conn.close()
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_reliability(data)
    return 0
