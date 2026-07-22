"""cost — agrégation du **coût token** d'un projet : par **step** (job) → **feature** → **projet**.

Constat (E2E live 2026-07-18) : un drain dispatche N workers `claude -p` (+ review), chacun consomme des
tokens, mais aucune vue coût agrégée n'existe. La donnée est déjà là : `record_finish` persiste, par job,
l'usage token (`input/output/cache_*`, extrait de l'event `result` du stream-json) + `cost_usd`
(= `total_cost_usd` de Claude, aux tarifs courants) + le modèle dominant. Ce module ne fait que **rouler** cet
usage sur le graphe `dispatch_jobs → tasks → features → projects` (FK). **On ne recalcule PAS le $** (pas de
table de prix locale qui périmerait) : le $ affiché est celui de Claude, les tokens la vérité, le $ le repère.

Règles d'attribution (cf. spec cockpit-token-cost-per-project-and-step) :
- **step** = jobs `task` d'une même task, **sommés** (les retries = plusieurs jobs pour une task : on somme).
- **fix** (`kind='fix'`) : rattaché à une task-ancre ARBITRAIRE (`worker._feature_anchor_task_id`) → agrégé au
  niveau **feature** (« fix (feature) »), jamais imputé à un step (l'ancre est trompeuse).
- **review/toolchain** (`kind`) : overhead PROJET (« review · outillage »), compté dans le total mais **hors**
  du coût par step de travail.
- jobs `failed`/`killed` sans usage → cost/token NULL → contribuent **0** (jamais un faux zéro synthétisé).
- **interview de socle** (v14) : une session `cockpit interview` est un `claude` INTERACTIF → **pas d'event
  `result`, donc pas de $** (Claude ne price pas l'interactif). Son transcript (`…/<sid>.jsonl`, ids sur
  `projects.interview_session_ids`) porte l'usage token par-tour → sommé (cumulatif, comme un `result`
  headless) en une ligne « interview / cadrage » **tokens-only** (`cost_usd=None`). On ne recalcule pas son
  $ (une table de prix périmerait — cf. la doctrine).

Réconciliation garantie : `total.cost_usd == Σ(features) + nonwork` (l'interview ne price pas) et
`total.tokens == Σ(features) + nonwork + interview` (les tokens interview grossissent le total) ;
`feature == Σ(steps) + fix`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from cockpit.db import store

if TYPE_CHECKING:
    from cockpit.config import Settings

# kinds « travail » (imputables à un step/feature) vs « hors-travail » (overhead projet).
_WORK_KINDS = ("task", "fix")
_NONWORK_KINDS = ("review", "toolchain")


def _blank() -> dict:
    """Accumulateur vierge (tokens à 0, pas None : une somme part de 0 ; l'absence d'usage d'un job = +0)."""
    return {"cost_usd": 0.0, "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
            "tokens": 0, "n_jobs": 0}


def _add(acc: dict, row: sqlite3.Row) -> None:
    """Ajoute l'usage d'un job à un accumulateur. Un champ NULL (run sans usage) compte 0 — jamais d'erreur,
    jamais un faux positif. `tokens` = somme des 4 types (la métrique globale affichée)."""
    i = row["input_tokens"] or 0
    o = row["output_tokens"] or 0
    cr = row["cache_read_tokens"] or 0
    cc = row["cache_creation_tokens"] or 0
    acc["cost_usd"] += row["cost_usd"] or 0.0
    acc["input"] += i
    acc["output"] += o
    acc["cache_read"] += cr
    acc["cache_creation"] += cc
    acc["tokens"] += i + o + cr + cc
    acc["n_jobs"] += 1


def _tok(v: object) -> int:
    """Un compte de tokens d'un transcript → int sûr (bool exclu, non-int → 0)."""
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _blank_interview() -> dict:
    """Accumulateur interview : `cost_usd=None` (NON pricé — pas de $, distinct d'un $0)."""
    return {"cost_usd": None, "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
            "tokens": 0, "model": None, "n_sessions": 0}


def _sum_interview_transcript(path: Path, acc: dict, model_out: dict[str, int]) -> None:
    """Somme l'usage token d'UN transcript d'interview (`~/.claude/projects/…/<sid>.jsonl`) dans `acc`. Une
    session interactive n'a pas d'event `result` : on somme `message.usage` PAR TOUR (cumulatif, comme le
    `result` headless). Modèle dominant = celui au plus d'`output` (`model_out`). Fail-soft ligne/ligne."""
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message")
                if not (isinstance(msg, dict) and isinstance(msg.get("usage"), dict)):
                    continue
                u = msg["usage"]
                i, o = _tok(u.get("input_tokens")), _tok(u.get("output_tokens"))
                cr, cc = _tok(u.get("cache_read_input_tokens")), _tok(u.get("cache_creation_input_tokens"))
                acc["input"] += i
                acc["output"] += o
                acc["cache_read"] += cr
                acc["cache_creation"] += cc
                acc["tokens"] += i + o + cr + cc
                m = msg.get("model")
                if isinstance(m, str) and m:
                    model_out[m] = model_out.get(m, 0) + o
    except OSError:
        return


def _interview_cost(conn: sqlite3.Connection, slug: str, *, home: Path | None = None) -> dict | None:
    """Coût token (tokens-only) des interviews de socle d'un projet, ou `None` si aucune session persistée /
    aucun transcript trouvé (honnête-vide). Résout chaque transcript par glob sur le `session_id` (robuste au
    teardown du worktree : l'encodage exact du cwd est indifférent)."""
    row = conn.execute("SELECT interview_session_ids FROM projects WHERE slug = ?", (slug,)).fetchone()
    raw = row[0] if row else None
    try:
        session_ids = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        session_ids = []
    if not session_ids:
        return None
    base = (home or Path.home()) / ".claude" / "projects"
    acc = _blank_interview()
    model_out: dict[str, int] = {}
    if base.is_dir():
        for sid in session_ids:
            hit = next(base.glob(f"*/{sid}.jsonl"), None)
            if hit is None:
                continue
            acc["n_sessions"] += 1
            _sum_interview_transcript(hit, acc, model_out)
    if acc["n_sessions"] == 0:
        return None                      # ids persistés mais transcripts absents (machine sans les .jsonl)
    acc["model"] = max(model_out, key=lambda m: model_out[m]) if model_out else None
    return acc


def project_cost(conn: sqlite3.Connection, slug: str, *, home: Path | None = None) -> dict:
    """Coût token agrégé d'un projet. `KeyError` si le projet n'existe pas. Un projet sans job → total à 0 +
    `features: []` + `nonwork` à 0 (honnête-vide, jamais une erreur). `home` (injectable en test) = racine où
    résoudre les transcripts d'interview (défaut `~`). Structure :
    `{project, total:{cost_usd,input,output,cache_read,cache_creation,tokens,n_jobs,model,n_models},
      features:[{slug, …acc, steps:[{task_slug, …acc}], fix:{…acc}|None}], nonwork:{…acc},
      interview:{cost_usd:None,input,output,cache_read,cache_creation,tokens,model,n_sessions}|None}`.
    L'interview (tokens-only, non pricé) grossit `total.tokens` mais PAS `total.cost_usd`."""
    if conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone() is None:
        raise KeyError(slug)
    rows = conn.execute(
        "SELECT f.slug AS feature, t.slug AS task, j.kind AS kind, j.model AS model, j.cost_usd, "
        "j.input_tokens, j.output_tokens, j.cache_read_tokens, j.cache_creation_tokens "
        "FROM dispatch_jobs j "
        "JOIN tasks t ON j.task_id = t.id "
        "JOIN features f ON t.feature_id = f.id "
        "JOIN projects p ON f.project_id = p.id "
        "WHERE p.slug = ? ORDER BY f.slug, t.slug", (slug,)).fetchall()

    total = _blank()
    nonwork = _blank()
    model_cost: dict[str, float] = {}
    feats: dict[str, dict] = {}
    order: list[str] = []

    for r in rows:
        _add(total, r)
        if r["model"]:
            model_cost[r["model"]] = model_cost.get(r["model"], 0.0) + (r["cost_usd"] or 0.0)
        if r["kind"] in _NONWORK_KINDS:
            _add(nonwork, r)
            continue
        f = feats.get(r["feature"])
        if f is None:
            f = {"acc": _blank(), "steps": {}, "step_order": [], "fix": None}
            feats[r["feature"]] = f
            order.append(r["feature"])
        _add(f["acc"], r)
        if r["kind"] == "fix":
            if f["fix"] is None:
                f["fix"] = _blank()
            _add(f["fix"], r)
        else:                                       # kind == 'task' → un step de travail
            s = f["steps"].get(r["task"])
            if s is None:
                s = _blank()
                f["steps"][r["task"]] = s
                f["step_order"].append(r["task"])
            _add(s, r)

    total["model"] = max(model_cost, key=lambda m: model_cost[m]) if model_cost else None
    total["n_models"] = len(model_cost)

    # Interview de socle (v14) : tokens-only (pas de $). Grossit le total EN TOKENS (in/out/cache + tokens),
    # jamais en $ (cost_usd/n_jobs intacts : pas un job pricé). None si aucune interview → total inchangé.
    interview = _interview_cost(conn, slug, home=home)
    if interview is not None:
        for k in ("input", "output", "cache_read", "cache_creation", "tokens"):
            total[k] += interview[k]

    features = [{"slug": fs, **feats[fs]["acc"],
                 "steps": [{"task_slug": ts, **feats[fs]["steps"][ts]} for ts in feats[fs]["step_order"]],
                 "fix": feats[fs]["fix"]}
                for fs in order]
    return {"project": slug, "total": total, "features": features, "nonwork": nonwork,
            "interview": interview}


# -- surface CLI ------------------------------------------------------------------------------------

def _fmt_usd(v: float | None) -> str:
    """$ lisible : 2 déc. dès $0.01 (repère projet), 4 en dessous (jobs bon marché). `None` = « — »
    (non pricé : l'interactif n'émet pas de $)."""
    if v is None:
        return "—"
    return f"${v:.2f}" if v >= 0.01 else f"${v:.4f}"


def _fmt_tokens(n: int) -> str:
    """Tokens compacts : `1.24M` / `480k` / brut en dessous de 1000."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _line(acc: dict) -> str:
    return f"{_fmt_usd(acc['cost_usd'])} · {_fmt_tokens(acc['tokens'])} tokens"


def _print_human(data: dict, *, by_step: bool) -> None:
    total = data["total"]
    model = f" · {total['model']}" if total.get("model") else ""
    nmod = f" ({total['n_models']} modèles)" if total.get("n_models", 0) > 1 else ""
    print(f"{data['project']} — {_line(total)}{model}{nmod}")
    if total["n_jobs"] == 0:
        print("  (aucun coût — lance un drain)")
        return
    for feat in data["features"]:
        print(f"  {feat['slug']:<28} {_line(feat)}")
        if by_step:
            for step in feat["steps"]:
                print(f"      {step['task_slug']:<24} {_line(step)}")
            if feat["fix"]:
                print(f"      {'fix (feature)':<24} {_line(feat['fix'])}")
    nw = data["nonwork"]
    if nw["n_jobs"]:
        print(f"  {'review · outillage':<28} {_line(nw)}")
    iv = data.get("interview")
    if iv:
        model = f" · {iv['model']}" if iv.get("model") else ""
        print(f"  {'interview · cadrage':<28} {_line(iv)}{model}")
        print("  (interview : tokens seuls — Claude ne price pas l'interactif ; le $ total reste le drain)")


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit cost <projet> [--by-step] [--json]` : total projet + rangées feature (et step si
    `--by-step`), ou le JSON brut de `project_cost`."""
    conn = store.open_db(settings)
    try:
        try:
            data = project_cost(conn, args.project)
        except KeyError:
            print(f"erreur : projet introuvable : {args.project}")
            return 2
    finally:
        conn.close()
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_human(data, by_step=getattr(args, "by_step", False))
    return 0
