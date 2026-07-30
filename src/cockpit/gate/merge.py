"""merge — merge d'une feature complète, **git internal-first** (SoT bare local, zéro `gh`/HTTP).

Deux concerns, frontière nette (même patron que le legacy) :
- **cœur PUR** : `compose_merge_decision` — la chaîne d'autorité (Tier-0 déterministe non-overridable →
  Tier-0 natif N/A-safe → Tier-1 review SHA-bound, garde de process non-overridable + 🔴 levable-humain →
  Tier-1.5 feature-verified conditionnel-UI → **GO humain = interrupteur final**). Portée **verbatim** du
  legacy `worker_merge_gate.compose_merge_decision`.
- **orchestration IMPURE** : `run_merge` — compose la décision (verdicts Tier-1/1.5 lus par `gate/review`
  et `gate/verify`, ancrés sur le SHA de la branche de feature) puis, **seulement si autorisé** (gate vert
  ET go humain), ff `feature→dev→main` (main-suit-dev), writeback identité injectée, **cleanup worktree
  AVANT `delete_branch`** (spec worktree-cleanup), et clôture DB (feature `merged`, tasks landées `done`).

INVARIANT DE SÛRETÉ (non négociable) : le LLM ne merge JAMAIS seul. `run_merge` ne mute RIEN tant que
`decision['allow']` est faux, et `allow` exige `human_go is True` EN PLUS d'un gate vert. Gate vert sans go
→ `hold` (bouton en attente), pas un merge. Merge irréversible → fail-CLOSED (Tier-1 absent/périmé → hold).

Port de `lib/worker_merge_gate.py` (décision pure) + `lib/forge_merge.py` (ordre merge→cleanup→close),
**délocalisé** (#3, hors monolithe) et **réécrit internal-first** (#8 : plus de couture GitHub/relais CT ;
`git/internal` fournit déjà `merge_ff`/`merge_writeback`/`remove_worktree`/`delete_branch` — `merge` COMPOSE,
il ne réimplémente pas le git). Refactor #13 (états de verdict sous config, via `review`/`verify`).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from cockpit.config import Settings
from cockpit.db import alerts, merge_outcomes
from cockpit.dispatch import worktree
from cockpit.gate import history, review, toolchain, verify, woaw
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects.registry import get_project
from cockpit.roadmap.model import resolve_feature
from cockpit.secrets import cred_resolver

BASE_BRANCH = "dev"     # base du cycle de merge (discipline de branche)
MAIN_BRANCH = "main"    # branche promue (ff depuis dev — main-suit-dev)


def blocker_tier(blockers: list[str]) -> str | None:
    """Déduit le tier de CONTEXTE (le plus fondamental) d'une liste de blockers de `compose_merge_decision`,
    pour l'affichage d'une alerte `gate_red`. None si aucun tier reconnu. Ordre : tier0 (filet dur) → tier1.5
    → tier1 ; « tier-1.5 » testé AVANT « tier-1 » (le second est un sous-mot du premier)."""
    joined = " ".join(blockers).lower()
    if "tier-0" in joined:
        return "tier0"
    if "tier-1.5" in joined:
        return "tier1.5"
    if "tier-1" in joined:
        return "tier1"
    return None


# -- cœur PUR : la chaîne d'autorité (portée verbatim de worker_merge_gate.compose_merge_decision) --

def compose_merge_decision(tier0_decision: dict, tier1_status: dict, *, human_go: bool,
                           t15_status: dict | None = None, ui_touched: bool = False,
                           t15_override: str = "", native_status: dict | None = None,
                           t1_override: str = "", code_touched: bool = True,
                           woaw_status: dict | None = None) -> dict:
    """Décision de merge : fusionne le filet **Tier-0** déterministe, la **toolchain native** du repo, le
    verdict **Tier-1** (review SHA-bound), le **Tier-1.5** (feature-verified, si UI touchée), et le **GO
    humain**. PUR.

    Conditions CUMULATIVES pour merger :
      - **Tier-0 propre** (0 🔴 déterministe) — NON-overridable (filet dur). Les 🟡 surfacés, non bloquants.
      - **Tier-0 natif propre** (si le repo a une toolchain native : `native_status['applicable']`) — exit 0.
        NON-overridable (déterministe : un veto bloquant est un fait, pas un avis). Repo sans toolchain → N/A.
      - **Tier-1 présent + FRAIS + PASS** (reviewed_sha == HEAD feature, 0 🔴) — **seulement si le diff porte
        de la source exécutable** (`code_touched`). Garde de PROCESS (non-overridable) : une revue doit avoir
        eu lieu sur le HEAD mergé. Un 🔴 reviewer BLOQUE — levable par override humain explicite
        (`t1_override`, raison non vide, tracé), JAMAIS le Tier-0/natif. Un livrable **docs-only** (prose
        seule) → Tier-1 **N/A** (rien à reviewer), non bloquant — symétrique au Tier-1.5 hors UI.
      - **Tier-1.5 (si `ui_touched`)** présent + FRAIS + rendu prouvé. Hors UI → N/A. Levable par override
        humain explicite (`t15_override`) — jamais automatique.
      - **human_go is True** — le LLM ne merge JAMAIS seul.

    Le **gate** et le **GO** sont SÉPARÉS dans le retour (`gate_green` vs `human_go`) : gate vert SANS go →
    `hold`. Fail-CLOSED : Tier-1/Tier-1.5 absent/périmé → hold. `refixable` = rouge par défaut de code frais
    qu'un worker peut corriger (`dispatch.refix`). Retour {allow, decision, gate_green, human_go, ui_touched,
    t15_overridden, t1_overridden, refixable, blockers, reasons}."""
    red0 = tier0_decision.get("red", 0)
    yellow0 = tier0_decision.get("yellow", 0)
    t15 = t15_status or {}
    nat = native_status or {}
    blockers: list[str] = []
    overrides: list[str] = []
    t15_overridden = False
    t1_overridden = False
    if red0:
        blockers.append(f"Tier-0 : {red0} 🔴 déterministe(s) (NON-overridable) → corriger avant merge")
    if nat.get("applicable") and not nat.get("ok"):     # toolchain native cassée → veto déterministe dur
        step = f", étape `{nat['failed_step']}`" if nat.get("failed_step") else ""
        blockers.append(f"Tier-0 natif : `{nat.get('cmd') or 'toolchain native'}` a échoué "
                        f"(exit {nat.get('exit_code')}{step}) → corriger avant merge (NON-overridable)")
    # Tier-1 (review dispatchée) : garde de process (non-overridable) — une revue FRAÎCHE doit exister sur le
    # HEAD ; absente/périmée ⇒ blocage. Un 🔴 reviewer BLOQUE — levable par override humain explicite tracé.
    # EXIGÉE seulement si le diff porte de la source exécutable (`code_touched`) : un livrable docs-only
    # (prose) n'a pas de code à reviewer → Tier-1 N/A (comme Tier-1.5 hors UI, Tier-0 natif sans toolchain).
    t1c = tier1_status.get("counts") or {}
    red1 = t1c.get("red", 0) if code_touched else 0
    if code_touched:
        if not tier1_status.get("present"):
            blockers.append("Tier-1 : aucune revue sur le HEAD → dispatcher le reviewer (garde de processus)")
        elif not tier1_status.get("fresh"):
            blockers.append("Tier-1 : revue périmée (reviewed_sha ≠ HEAD) → re-dispatcher (garde processus)")
        elif red1:
            if t1_override:
                t1_overridden = True
                overrides.append(f"Tier-1 : {red1} 🔴 reviewer — OVERRIDE humain : {t1_override}")
            else:
                blockers.append(f"Tier-1 : {red1} 🔴 reviewer (le rapport bloque le merge) → corriger, "
                                f"ou override humain explicite avec raison")

    # Tier-1.5 : exigé SEULEMENT si la feature touche une surface UI.
    if ui_touched:
        if not t15.get("present") or not t15.get("fresh"):
            why = "absente" if not t15.get("present") else "périmée (reviewed_sha ≠ HEAD)"
            if t15_override:
                t15_overridden = True
                overrides.append(f"Tier-1.5 : preuve e2e {why} — OVERRIDE humain : {t15_override}")
            else:
                blockers.append(f"Tier-1.5 : preuve e2e {why} alors que l'UI est touchée → prouver le "
                                f"rendu (feature-verified) ou override humain explicite")
        elif t15.get("blocking"):
            nf = t15.get("n_failed")
            if t15_override:
                t15_overridden = True
                overrides.append(f"Tier-1.5 : {nf} cible(s) non rendue(s) — OVERRIDE humain : {t15_override}")
            else:
                blockers.append(f"Tier-1.5 : {nf} cible(s) non rendue(s) (le résultat ne s'affiche pas) → "
                                f"corriger le rendu avant merge")

    gate_green = not blockers
    # « refixable » : le gate est rouge à cause d'un défaut de CODE FRAIS qu'un worker de correction peut
    # traiter (Tier-0 natif cassé · 🔴 reviewer frais non-overridé · 🔴 Tier-0 déterministe). PUR, dérivé (pas
    # d'état) — consommé par `dispatch.refix` pour décider s'il OFFRE une passe. Un rouge « review absente/
    # périmée » n'est PAS refixable (re-dispatcher la review, pas corriger le code) ; un overridé non plus.
    refixable = bool(
        red0
        or (nat.get("applicable") and not nat.get("ok"))
        or (code_touched and tier1_status.get("present") and tier1_status.get("fresh")
            and red1 and not t1_overridden))
    reasons = list(blockers) + overrides
    if not code_touched:
        reasons.append("Tier-1 : N/A (aucune source exécutable — livrable docs-only)")
    elif tier1_status.get("present") and tier1_status.get("fresh") and not red1:
        yel1, pur1 = t1c.get("yellow", 0), t1c.get("purple", 0)
        if yel1 or pur1:
            reasons.append(f"Tier-1 : {yel1} 🟡 · {pur1} 🟣 reviewer (consultatif) — voir findings")
        else:
            reasons.append("Tier-1 : revue fraîche, aucun finding (PASS)")
    if yellow0:
        reasons.append(f"Tier-0 : {yellow0} 🟡 ruff/mypy introduit(s) (surfacé, ne bloque pas)")
    if nat.get("applicable") and nat.get("ok"):
        reasons.append(f"Tier-0 natif : `{nat.get('cmd') or 'toolchain native'}` ✓ (vérif native passée)")
    elif not nat.get("applicable"):
        reasons.append("Tier-0 natif : N/A (pas de toolchain native détectée)")
    if not ui_touched:
        reasons.append("Tier-1.5 : N/A (aucune surface UI touchée)")
    # Axe woaw (esthétique) — ADVISORY : surfacé en consultatif, jamais dans `blockers`. Un rendu plat/faible
    # informe le GO humain sans jamais tenir le merge (doctrine §4 « advisory d'abord » ; promotion en
    # bloquant-overridable = choix futur quand le flake du juge est mesuré bas). N'affecte NI `gate_green` NI
    # `allow` — il est lu APRÈS le calcul du gate.
    wo = woaw_status or {}
    if ui_touched and wo.get("present") and wo.get("fresh"):
        wc = wo.get("counts") or {}
        flat = " · seuil de plat atteint" if wo.get("flat") else ""
        reasons.append(f"Axe woaw (consultatif) : {wc.get('red', 0)} 🔴 · {wc.get('yellow', 0)} 🟡 · "
                       f"{wc.get('purple', 0)} 🟣 sur {wo.get('route', '/')}{flat} — ne bloque pas")
    elif ui_touched:
        reasons.append("Axe woaw : N/A (aucun verdict esthétique frais — consultatif, ne bloque pas)")
    if gate_green and not human_go:
        reasons.append("Gate vert — en attente du GO humain au cockpit (le LLM ne merge jamais seul)")
    elif gate_green and human_go:
        reasons.append("Gate vert + GO humain → merge autorisé")

    allow = gate_green and bool(human_go)
    return {"allow": allow, "decision": "merge" if allow else "hold",
            "gate_green": gate_green, "human_go": bool(human_go),
            "ui_touched": bool(ui_touched), "t15_overridden": t15_overridden,
            "t1_overridden": t1_overridden, "refixable": refixable,
            "blockers": blockers, "reasons": reasons}


# -- orchestration IMPURE : run_merge (internal-first) ----------------------------------------------

def _close_feature_tasks(conn: sqlite3.Connection, feature_id: str) -> list[str]:
    """Writeback DB post-merge : la feature est `merged`, ses tasks landées (`in_progress`) passent `done`.
    Retourne les slugs fermés. Les tasks jamais dispatchées (`todo`) restent telles quelles (surfacées)."""
    rows = conn.execute("SELECT slug FROM tasks WHERE feature_id = ? AND status = 'in_progress'",
                        (feature_id,)).fetchall()
    closed = [r["slug"] for r in rows]
    conn.execute("UPDATE tasks SET status = 'done' WHERE feature_id = ? AND status = 'in_progress'",
                (feature_id,))
    conn.execute("UPDATE features SET status = 'merged' WHERE id = ?", (feature_id,))
    conn.commit()
    return closed


def evaluate_gate(conn: sqlite3.Connection, settings: Settings, *, feature_ref: str, human_go: bool,
                  git: InternalGit | None = None, tier0: dict | None = None, native: dict | None = None,
                  t1_override: str = "", t15_override: str = "") -> dict:
    """Évalue le gate SANS RIEN MUTER : résout le SHA de la branche de feature, lit les verdicts Tier-1
    (review) et Tier-1.5 (verify) ancrés sur ce SHA, détecte si l'UI est touchée, compose la décision
    (`compose_merge_decision`). **Source unique** de l'évaluation : `run_merge` la réutilise (puis agit si
    `allow`) et le GET gate l'expose en *preview* (`human_go=False` → `hold`) pour rendre l'état « gate vert
    sans GO » sans jamais POSTer un merge. Retourne `{feature, project_slug, feature_slug, branch, sot,
    head_sha, ui_touched, tier1_status, t15_status, decision}` ; `head_sha`/`decision`=None si la feature n'a
    pas de branche (jamais dispatchée)."""
    git = git or InternalGit()
    project_slug, feature_slug = feature_ref.split("/", 1) if "/" in feature_ref else ("", feature_ref)
    feature = resolve_feature(conn, feature_ref)          # KeyError/ValueError si absent
    branch = feature["branch"]
    sot = Path(get_project(conn, project_slug)["sot_path"])
    try:
        head_sha: str | None = git.feature_sha(sot, branch)   # ancre de fraîcheur des verdicts
    except GitOpError:
        head_sha = None                                       # jamais dispatchée → pas encore de branche
    tier1_status = review.status(settings, project_slug, feature_slug, current_sha=head_sha)
    t15_status = verify.status(settings, project_slug, feature_slug, current_sha=head_sha)
    woaw_status = woaw.status(settings, project_slug, feature_slug, current_sha=head_sha)  # advisory
    ui_touched = False
    decision: dict | None = None
    native_status: dict | None = None                         # Tier-0 natif (toolchain) — surfacé au GET gate
    if head_sha is not None:
        diff_files = git.diff_names(sot, base=BASE_BRANCH, head=branch)   # unique appel — UI + natif + N/A
        # Tier-1.5 : trigger hybride (nom + contenu) — un `.tsx` de câblage/type n'est PAS un vrai visuel.
        ui_touched = verify.has_visual_change(diff_files, git.diff_text(sot, base=BASE_BRANCH, head=branch))
        code_touched = toolchain.has_reviewable_code(diff_files)   # docs-only OU diff vide → Tier-1 N/A
        # Tier-0 natif : injecté (tests) sinon LU depuis le verdict toolchain SHA-bound (jamais exécuté ici —
        # evaluate_gate est appelé par le GET gate poll-é ; l'exécution est un step séparé, cf. toolchain).
        native_status = native if native is not None else toolchain.status(
            settings, project_slug, feature_slug, current_sha=head_sha, diff_files=diff_files)
        decision = compose_merge_decision(
            tier0 or {"red": 0, "yellow": 0}, tier1_status, human_go=human_go,
            t15_status=t15_status if ui_touched else None, ui_touched=ui_touched,
            t15_override=t15_override, native_status=native_status, t1_override=t1_override,
            code_touched=code_touched, woaw_status=woaw_status if ui_touched else None)
    return {"feature": feature, "project_slug": project_slug, "feature_slug": feature_slug,
            "branch": branch, "sot": sot, "head_sha": head_sha, "ui_touched": ui_touched,
            "tier1_status": tier1_status, "t15_status": t15_status, "native_status": native_status,
            "woaw_status": woaw_status, "decision": decision}


def run_merge(conn: sqlite3.Connection, settings: Settings, *, feature_ref: str, human_go: bool,
              git: InternalGit | None = None, tier0: dict | None = None, native: dict | None = None,
              t1_override: str = "", t15_override: str = "") -> dict:
    """Merge de `feature_ref` (`"projet/feature"`) SOUS GO HUMAIN, internal-first. Retourne un rapport
    `{merged, allow, decision, feature, head_sha, merge_sha, closed_tasks, pending_tasks, reason}`.

    Évalue le gate via `evaluate_gate` (Tier-0 injecté défaut propre ; Tier-0 natif injecté défaut N/A ;
    Tier-1/Tier-1.5 lus, ancrés sur le SHA de la branche de feature). **Aucune mutation** tant que
    `decision['allow']` est faux. Si autorisé : ff `feature→dev`, ff `dev→main` (main-suit-dev), writeback
    identité injectée (miroir best-effort), **`worktree.release` AVANT `delete_branch`**, puis clôture DB."""
    # git non injecté → InternalGit doté du résolveur de credentials (writeback authentifié par la réf du
    # projet, résolue à l'usage). Injecté (tests) → laissé tel quel (I/O contrôlée).
    git = git or InternalGit(cred_resolver=cred_resolver(settings))
    ev = evaluate_gate(conn, settings, feature_ref=feature_ref, human_go=human_go, git=git,
                       tier0=tier0, native=native, t1_override=t1_override, t15_override=t15_override)
    feature, feature_slug = ev["feature"], ev["feature_slug"]
    project_slug, branch, sot, head_sha = ev["project_slug"], ev["branch"], ev["sot"], ev["head_sha"]
    # `decision` est composée par `compose_merge_decision` — PURE. On ne la persiste PAS : fonction pure +
    # entrées (verdicts) historisées = décision REJOUABLE. La persister dupliquerait une sortie déterministe.
    # Ce qui mérite capture, c'est le GO humain (fait daté, non-rejouable) → historisé au succès du merge.
    decision = ev["decision"]

    # Une feature planifiée mais jamais dispatchée n'a pas encore de branche git (créée au 1ᵉʳ worktree) →
    # rien à merger. Outcome DOMAINE propre (pas un traceback) : `decision=None`, la CLI le rend lisible.
    if decision is None:
        return {"merged": False, "allow": False, "decision": None, "feature": feature_slug,
                "head_sha": None, "merge_sha": None, "closed_tasks": [], "pending_tasks": [],
                "reason": f"branche {branch} absente — feature jamais dispatchée (rien à merger)"}

    report = {"merged": False, "allow": decision["allow"], "decision": decision,
              "feature": feature_slug, "head_sha": head_sha, "merge_sha": None,
              "closed_tasks": [], "pending_tasks": [], "reason": decision["decision"]}
    if not decision["allow"]:                             # garde de sûreté : rien ne mute
        report["reason"] = ("en attente du GO humain" if decision["gate_green"]
                            else "gate rouge — " + "; ".join(decision["blockers"]))
        if not decision["gate_green"]:                    # gate ROUGE (≠ vert-sans-GO, pas un blocage)
            blk = decision["blockers"]
            alerts.emit_alert(conn, project=project_slug, feature_ref=feature_ref, feature=feature_slug,
                              kind="gate_red", severity="blocker",
                              reason=blk[0] if blk else "gate incomplet",
                              tier=blocker_tier(blk), findings=blk)
        return report

    # (1a) réalignement anti-stale-base (cockpit-merge-batched-sibling-stale-base) : quand ≥2 features
    # siblings branchent du même `dev` (drain parallèle) et se mergent en batch, le 1er merge fait avancer
    # `dev` → `dev` n'est plus ancêtre de la 2ᵉ → le ff strict lèverait. On rebase la branche sur le `dev`
    # à jour (linéaire, préserve les commits worker) AVANT le ff. Rebase trivial (fichiers disjoints) → le
    # diff `dev...HEAD` est préservé, donc le verdict SHA-bound déjà validé par `evaluate_gate` reste
    # valide ; on ré-ancre `head_sha` sur le nouveau HEAD. Conflit non trivial → `rebase_onto` lève.
    if not git.is_ancestor(sot, BASE_BRANCH, branch):
        wt = worktree.worktree_path_for(settings, project_slug, feature_slug)
        git.rebase_onto(sot, wt, onto=BASE_BRANCH,
                        identity=resolve_identity(project_slug, BASE_BRANCH, role="worker"))
        head_sha = git.feature_sha(sot, branch)
    # (1b) merge ff feature→dev, puis dev→main (main-suit-dev). Un non-ff lève (GitOpError) → surfacé.
    git.merge_ff(sot, into=BASE_BRANCH, source=branch)
    git.merge_ff(sot, into=MAIN_BRANCH, source=BASE_BRANCH)
    # (2) writeback : identité injectée (non persistée) + push miroir best-effort si un remote existe.
    # La réf de credential du projet (opaque, jamais le token) est passée au writeback ; le résolveur la
    # convertit en token injecté le temps du push (0 secret en DB — spec merge-writeback).
    creds_ref = get_project(conn, project_slug).get("credential_ref")
    git.merge_writeback(sot, creds_ref=creds_ref, identity=resolve_identity(project_slug, BASE_BRANCH))
    # (3) cleanup worktree AVANT delete_branch (spec worktree-cleanup) : release retire le worktree + relâche
    # le port ; delete_branch supprime ensuite la branche (libérée de son worktree).
    worktree.release(conn, settings, git, project=project_slug, feature=feature_slug)
    git.delete_branch(sot, branch)
    # (3b) trace : capture le GO humain (fait daté, non-rejouable) puis BORNE l'historique de la feature — au
    # même point que `delete_branch`, qui sinon orpheline les verdicts. Best-effort (jamais un merge raté).
    history.record_verdict(conn, project_slug, feature_slug, "merge",
                           {"reviewed_sha": head_sha, "human_go": human_go, "reason": "merge"})
    history.prune_verdicts(conn, project_slug, feature_slug)
    # (3c) instrumentation d'outcome : ancre DURABLE et survivante du merge vert (dénominateur de fiabilité) —
    # hors du journal borné, l'issue aval sera marquée APRÈS coup. Best-effort (cf. db/merge_outcomes).
    merge_outcomes.record_merge(conn, project=project_slug, feature=feature_slug, feature_ref=feature_ref,
                                sha=head_sha, human_go=human_go)
    # (4) clôture DB : feature merged, tasks landées done.
    report["closed_tasks"] = _close_feature_tasks(conn, feature["id"])
    # Ancre de résolution DÉFINITIVE : une feature mergée ne re-bloque jamais → toute alerte ouverte tombe.
    alerts.resolve_alerts(conn, project=project_slug, feature_ref=feature_ref)
    report["pending_tasks"] = [r["slug"] for r in conn.execute(
        "SELECT slug FROM tasks WHERE feature_id = ? AND status = 'todo'", (feature["id"],)).fetchall()]
    report.update(merged=True, merge_sha=head_sha, reason="merge")
    return report


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit merge <feature>` : compose le gate et — sous GO humain (`--go`) — merge ff
    feature→dev→main, cleanup worktree, writeback. Sans `--go`, un gate vert affiche `hold` (bouton actif)."""
    from cockpit.db import store

    conn = store.open_db(settings)
    try:
        report = run_merge(conn, settings, feature_ref=args.feature,
                           human_go=bool(getattr(args, "go", False)))
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    if report["merged"]:
        n_pend = len(report["pending_tasks"])
        pend = f" · {n_pend} task(s) todo restante(s)" if n_pend else ""
        print(f"merge OK : {args.feature} → dev/main (sha {report['merge_sha'][:8]}) — "
              f"{len(report['closed_tasks'])} task(s) fermée(s){pend}")
        return 0
    decision = report["decision"]
    if decision is None:                                  # feature jamais dispatchée / rien à merger
        print(f"🔴 {report['reason']}")
        return 1
    for r in decision["reasons"]:
        print(f"  {r}")
    icon = "🟡" if decision["gate_green"] else "🔴"
    print(f"{icon} {report['reason']}")
    return 0 if decision["gate_green"] else 1
