"""refix — **passe de correction** sur un gate ROUGE (le chaînon manquant de la boucle autonome : findings →
correction → re-review). Un gate rouge n'est plus un cul-de-sac : `dispatch_refix` **envisage** et, sur offre
explicite (bouton UI / `cockpit refix`), **dispatche UNE passe** de worker qui corrige les bloqueurs cités sur
la **branche de feature** (« retour au dev ») — le brief **est** le verdict — puis **re-finalise** (Tier-0 +
review re-dispatchée, verdict ré-ancré sur le nouveau HEAD).

**Cadre (bosse) : offre, pas autonomie sauvage.** Une passe par appel (jamais un re-dispatch en spin) ;
**bornée** à `MAX_FIX_PASSES` par feature (au-delà → stop propre + remontée humaine) ; le **merge ne
s'automatise JAMAIS** (GO humain, fail-closed — `refix` agit sur le verdict rouge, il ne merge pas).

Compose des primitives existantes : `merge.evaluate_gate` (lecture pure du gate + `refixable`),
`worker.dispatch_fix` (worker de correction, worktree réutilisé/rebasé), `orchestrator.finalize_feature`
(Tier-0 + reviewer re-dispatché → gate ré-évalué), `jobs.count_fix_jobs` (la borne). I/O injectable (`git`,
`runner`, `review_runner`) → testable sans vrai `claude`.
"""
from __future__ import annotations

import argparse
import sqlite3
from typing import TYPE_CHECKING

from cockpit.db import store
from cockpit.dispatch import jobs, orchestrator, reviewer, worker
from cockpit.gate import merge, review, toolchain
from cockpit.git.internal import InternalGit

if TYPE_CHECKING:
    from cockpit.config import Settings

MAX_FIX_PASSES = 3   # borne de passes de correction par feature — au-delà : stop propre + remontée humaine


def _result(status: str, *, feature: str, next_step: str, gate_green: bool = False,
            fix_pass: int = 0, blockers: list[str] | None = None, head_sha: str | None = None) -> dict:
    """Compte-rendu normalisé (contrat front `RefixResultSchema`)."""
    return {"status": status, "feature": feature, "gate_green": gate_green,
            "fix_pass": fix_pass, "max_passes": MAX_FIX_PASSES,
            "blockers": list(blockers or []), "head_sha": head_sha, "next_step": next_step}


def _not_refixable_next(blockers: list[str]) -> str:
    """Prochaine étape quand le rouge n'est PAS corrigible par un worker (garde de process, pas du code)."""
    joined = " ".join(blockers)
    if "aucune revue" in joined or "périmée" in joined:
        return "re-dispatcher le reviewer (Tier-1) — le rouge est une garde de processus, pas du code."
    return "reprends la main : override humain explicite, ou bloqueur non corrigible par un worker."


def dispatch_refix(conn: sqlite3.Connection, settings: Settings, *, project: str, feature: str,
                   git: InternalGit | None = None, runner: worker.Runner | None = None,
                   review_runner: reviewer.Runner | None = None) -> dict:
    """UNE passe de correction bornée sur un gate rouge de `project/feature`. Lit le gate (preview, ne mute
    pas), refuse honnêtement si non-rouge / non-refixable / borne atteinte (0 spawn), sinon dispatche le
    worker de fix (brief = findings) → re-finalise → ré-évalue le gate. Retour = compte-rendu
    (`RefixResultSchema`). Le merge n'est JAMAIS déclenché ici (GO humain, hors boucle)."""
    git = git or InternalGit()
    feature_ref = f"{project}/{feature}"
    ev = merge.evaluate_gate(conn, settings, feature_ref=feature_ref, human_go=False, git=git)
    decision = ev.get("decision")
    if not decision:                                       # jamais dispatchée → pas de branche/verdict
        return _result("not_red", feature=feature,
                       next_step="feature jamais dispatchée — rien à corriger (draine-la d'abord).")
    blockers = list(decision.get("blockers", []))
    if decision.get("gate_green"):
        return _result("not_red", feature=feature, gate_green=True, head_sha=ev.get("head_sha"),
                       next_step="gate vert — pas de correction à dispatcher (merge sous ton GO).")
    if not decision.get("refixable"):
        return _result("not_refixable", feature=feature, blockers=blockers, head_sha=ev.get("head_sha"),
                       next_step=_not_refixable_next(blockers))

    n_fix = jobs.count_fix_jobs(conn, ev["feature"]["id"])   # passes DÉJÀ dépensées
    if n_fix >= MAX_FIX_PASSES:
        return _result("exhausted", feature=feature, blockers=blockers, fix_pass=n_fix,
                       head_sha=ev.get("head_sha"),
                       next_step=(f"borne de {MAX_FIX_PASSES} passes atteinte — reprends la main "
                                  "(corrige à la main, override explicite, ou abandonne la feature)."))

    # Brief = le verdict : findings 🔴 reviewer + steps Tier-0 en échec, lus depuis les fichiers courants.
    findings = {"review": review.read_verdict(settings, project, feature),
                "toolchain": toolchain.read_verdict(settings, project, feature)}
    fix = worker.dispatch_fix(conn, settings, feature_ref=feature_ref, findings=findings,
                              git=git, runner=runner)
    fix_pass = n_fix + 1
    if not fix.get("dispatched") or not (fix.get("result") or {}).get("ok"):
        reason = fix.get("reason") or "échec du worker de correction"
        return _result("dispatch_failed", feature=feature, blockers=blockers, fix_pass=fix_pass,
                       head_sha=ev.get("head_sha"),
                       next_step=f"le worker de correction a échoué ({reason}) — re-tentable, voir le job.")

    # Re-finalise sur le nouveau HEAD (Tier-0 + reviewer re-dispatché) PUIS ré-évalue le gate.
    orchestrator.finalize_feature(conn, settings, project, feature, review_runner=review_runner)
    ev2 = merge.evaluate_gate(conn, settings, feature_ref=feature_ref, human_go=False, git=git)
    d2 = ev2.get("decision") or {}
    blk2 = list(d2.get("blockers", []))
    head2 = ev2.get("head_sha")
    if d2.get("gate_green"):
        return _result("green", feature=feature, gate_green=True, fix_pass=fix_pass, head_sha=head2,
                       next_step="gate re-évalué VERT — merge sous ton GO (le LLM ne merge jamais seul).")
    if not d2.get("refixable"):
        return _result("not_refixable", feature=feature, blockers=blk2, fix_pass=fix_pass, head_sha=head2,
                       next_step=_not_refixable_next(blk2))
    if fix_pass >= MAX_FIX_PASSES:
        return _result("exhausted", feature=feature, blockers=blk2, fix_pass=fix_pass, head_sha=head2,
                       next_step=(f"toujours rouge après {MAX_FIX_PASSES} passes — reprends la main "
                                  "(corrige à la main, override explicite, ou abandonne la feature)."))
    return _result("still_red", feature=feature, blockers=blk2, fix_pass=fix_pass, head_sha=head2,
                   next_step=(f"toujours rouge — tu peux relancer une passe ({MAX_FIX_PASSES - fix_pass} "
                              "restante(s)) ou reprendre la main."))


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit refix <project> <feature>` : UNE passe de correction bornée sur un gate rouge, imprime
    le compte-rendu (statut, passe n/N, bloqueurs, prochaine étape). Parité spine (toute capacité daemon
    existe d'abord en CLI). Exit 0 (l'issue vit dans le statut imprimé)."""
    conn = store.open_db(settings)
    try:
        r = dispatch_refix(conn, settings, project=args.project, feature=args.feature)
    finally:
        conn.close()
    print(f"refix {args.project}/{args.feature} : {r['status']} "
          f"(passe {r['fix_pass']}/{r['max_passes']})")
    for b in r["blockers"]:
        print(f"  🔴 {b}")
    print(f"  → {r['next_step']}")
    return 0
