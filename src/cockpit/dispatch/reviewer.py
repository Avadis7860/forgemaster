"""reviewer — dispatch du **review-worker Tier-1** (rôle `function: review` de la charte
`generic-orchestrator-charter`). Le worker de la feature GÉNÈRE ; ce reviewer GATE — il **ne code jamais**
(read-only, `work=False`), invariant « LLM génère / déterministe gate ». Il lit le diff `dev...branche`,
distille un verdict **commission-only** (🔴 = ligne écrite citée verbatim, fausse/cassée ; omission = 🟡 max),
et l'écrit **SHA-bound** via `gate/review.write_verdict` (garde `evidence⊂diff` déjà là). Consommé par
`gate/merge` comme Tier-1.

Porte le pattern **prouvé live** de `[[merge-go-feedback-and-reviewer-wiring]]` (reviewer auto post-travail,
idempotent, best-effort) dans le cockpit lightweight — la keystone que le pivot n'avait pas re-portée. Le
transport (`runner`) est **injectable** → les tests ne spawnent jamais un vrai `claude`.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from cockpit import auth
from cockpit.config import Settings
from cockpit.core import ids, run
from cockpit.dispatch import jobs, worker
from cockpit.dispatch import worktree as worktree_mod
from cockpit.gate import review
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects.registry import sot_path_for
from cockpit.provision import facet as facet_mod
from cockpit.provision.mcp import inject_mcp_config
from cockpit.roadmap import model, resolver
from cockpit.tools import ToolPreflightError, preflight_tools, tools_env

REVIEW_FACET = "review"
REVIEW_BASE = "dev"                 # base du diff reviewé (main-suit-dev)
REVIEW_TIMEOUT = 900.0              # s ; une review lit un diff borné — plus court qu'un run de travail

# runner(argv, *, cwd, input_text, timeout, env) -> RunResult. Défaut = subprocess local ; injecté en test.
Runner = Callable[..., run.RunResult]


def _default_runner(argv: list[str], *, cwd: object, input_text: str, timeout: float,
                    env: Mapping[str, str] | None = None) -> run.RunResult:
    return run.run(argv, cwd=cwd, input_text=input_text, timeout=timeout, env=env)   # type: ignore[arg-type]


# -- prompt du reviewer (commission-only, porté de la doctrine /diff-review) -------------------------

def build_review_prompt(worktree_root: object, feature: dict, tasks: list[dict], diff_text: str) -> str:
    """Compose le prompt du reviewer : persona/méthode de la facette `review` (committées dans le worktree),
    le mandat **commission-only**, le **cadrage** (Objectif/DoD des tasks de la feature — pas une checklist de
    complétude), et le **diff** à juger. Le reviewer rend ses findings en JSON (contrat `write_verdict`). PUR
    (hors lecture des `.md` de facette). Le prompt part sur le stdin de `claude -p`."""
    persona = _facet_md(worktree_root, "PERSONA.md")
    method = _facet_md(worktree_root, "METHOD.md")
    dod = "\n".join(f"- **{t['slug']}** : {(t.get('acceptance') or '').strip() or '(pas de DoD explicite)'}"
                    for t in tasks)
    mandate = (
        "Tu es un **reviewer Tier-1** dispatché en headless (aucun interlocuteur). Tu JUGES un diff, tu ne "
        "modifies RIEN (lecture seule : `Read`/`Grep`/`Glob`, `codemap where` pour situer). Doctrine "
        "**commission-only** : un 🔴 ne porte QUE sur une **commission** — une ligne AJOUTÉE par le diff, "
        "citée **verbatim**, qui est fausse/cassée (y compris une suppression qui casse l'existant). Une "
        "**omission** (code pas encore câblé, DoD partielle car la suite est une autre task) = 🟡 au plus, "
        "jamais 🔴 ; dans le doute → 🟡. La **complétude/le séquencement appartiennent à l'humain qui "
        "merge** — ne bloque pas sur du manquant. Les critères d'acceptation ci-dessous sont un **cadrage** "
        "(de quoi parle la feature), pas une checklist à cocher.\n\n"
        "Pour CHAQUE finding : tente de le **réfuter** en lisant le code autour ; s'il ne survit pas, "
        "jette-le. Rends **UNIQUEMENT** un objet JSON final (dernier bloc), sans autre texte après :\n"
        "```json\n"
        "{\"base\": \"dev\", \"findings\": [\n"
        "  {\"severity\": \"🔴\", \"category\": \"correctness\", \"file\": \"chemin\", \"line\": 12,\n"
        "   \"claim\": \"ce qui est faux, en une phrase\",\n"
        "   \"evidence\": \"chemin:12 — <citation VERBATIM d'une ligne + du diff>\",\n"
        "   \"verify_note\": \"réfutation tentée : … → confirmé\"}\n"
        "]}\n"
        "```\n"
        "`evidence` DOIT contenir, après le tiret «—», une citation **verbatim** d'une ligne ajoutée (`+`) "
        "du diff : un finding non citable est rejeté à la source. Diff propre → `\"findings\": []`."
    )
    branch = feature.get("branch", "")
    blocks = [
        f"# Review Tier-1 — feature `{feature['slug']}` (branche `{branch}`)",
        persona, mandate, method,
        "## Cadrage — Objectif/DoD des tasks (contexte, PAS une checklist de complétude)\n"
        + (dod or "(aucun)"),
        f"## Diff à juger (`dev...{branch}`)\n```diff\n{diff_text}\n```",
    ]
    return "\n\n".join(b for b in blocks if b) + "\n"


def _facet_md(root: object, leaf: str) -> str:
    doc = facet_mod.facet_dir(root, REVIEW_FACET) / leaf   # type: ignore[arg-type]
    if not doc.is_file():
        return ""
    try:
        return doc.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _extract_findings(result_text: str | None) -> list[dict]:
    """Extrait la liste `findings` de la sortie du reviewer (dernier objet JSON portant `findings`). Tolérant
    au préambule (le reviewer peut raisonner avant le bloc JSON final) et aux fences ```json. Vide si rien
    d'exploitable (un reviewer muet ⇒ 0 finding ⇒ verdict PASS — le fail-closed du gate est la fraîcheur)."""
    if not result_text:
        return []
    text = result_text.strip()
    # scanne chaque `{` en repartant du plus tardif (le bloc final) vers le début → le 1er qui parse gagne.
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for i in reversed(starts):
        candidate = text[i:]
        # tente aussi de couper une fence ``` de fin
        for end in (len(candidate), candidate.rfind("```")):
            if end <= 0:
                continue
            try:
                obj = json.loads(candidate[:end])
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
                return obj["findings"]
    return []


# -- readiness-gate + dispatch (cockpit-review-readiness-gate) ---------------------------------------

# États taskmap **terminaux** d'une task (le worker n'a plus rien à y faire) : `DONE` (accompli) ou
# `CANCELLED` (abandonné volontairement). Tout autre état — `READY`/`BLOCKED_DEPS`/`ACTIVE`/`BLOCKED`/
# `ERROR`/`CYCLE` — signale du travail restant ou coincé → la review serait prématurée (faux-positifs).
_TERMINAL_STATES = ("DONE", "CANCELLED")


def _readiness(conn: sqlite3.Connection, feature_id: str) -> tuple[bool, str]:
    """La feature est-elle **prête pour la review** ? Gate déterministe (charte : ne pas reviewer un travail
    inachevé → faux-positifs). Prêt ssi elle a ≥1 task ET **toutes** ses tasks sont dans un état taskmap
    terminal (`DONE`/`CANCELLED`). L'état vient de `resolver.classify` — la **même autorité de séquencement**
    que le DAG (une seule source de vérité de « task faite ») : ainsi un `blocked`/`todo`-non-résolu →
    `BLOCKED`/`READY`/`BLOCKED_DEPS`, non terminal → hold (l'ancien check `todo`/`in_progress` laissait passer
    un `blocked`). Aucune heuristique métier : le signal de complétude générique EST la terminalité du statut,
    l'ORDRE reste l'affaire du DAG `depends_on`. Retourne (ok, raison)."""
    tasks = model.list_tasks(conn, feature_id)
    if not tasks:
        return False, "aucune task dans la feature — rien à reviewer"
    classified = resolver.classify({t["slug"]: t for t in tasks})
    pending = {slug: c["state"] for slug, c in classified.items() if c["state"] not in _TERMINAL_STATES}
    if pending:
        detail = ", ".join(f"{slug}={state}" for slug, state in sorted(pending.items()))
        return False, (f"travail inachevé — {len(pending)} task(s) non terminée(s) ({detail}) → hold "
                       "(pas de review)")
    return True, "travail complet"


def _now_iso() -> str:
    """Horodatage ISO-UTC LOCAL au reviewer (pas le `_now` de `jobs`) : le puits de non-run reçoit son `ts` en
    argument (injectable en test), pas rejoué par une horloge interne à l'INSERT (`dispatch-jobs-journal`)."""
    return datetime.now(UTC).isoformat()


def _journal_non_run(conn: sqlite3.Connection, feature_ref: str, reason: str, *,
                     created_at: str | None = None) -> None:
    """Trace best-effort d'un run reviewer qui **n'a pas eu lieu** (journal PUR `non_runs`, `kind='review'`).
    **Best-effort, non négociable** : une trace ratée ne masque ni ne casse le résultat métier → `try/except`
    + warning (cf. `dispatch-jobs-journal`). `created_at` **injecté** (défaut = maintenant). Jamais lu par une
    garde — c'est un journal de mesure, pas un verrou d'idempotence."""
    try:
        conn.execute("INSERT INTO non_runs (id, feature_ref, kind, reason, created_at) "
                     "VALUES (?, ?, 'review', ?, ?)",
                     (ids.new_id(), feature_ref, reason, created_at or _now_iso()))
        conn.commit()
    except sqlite3.Error as exc:                             # best-effort : une trace ratée ne casse rien
        logging.getLogger("cockpit").warning("non_run non journalisé (%s) : %s", feature_ref, exc)


def dispatch_reviewer(conn: sqlite3.Connection, settings: Settings, *, feature_ref: str,
                      git: InternalGit | None = None, runner: Runner | None = None) -> dict:
    """Dispatche le reviewer Tier-1 sur `feature_ref` (`"projet/feature"`) **si le travail est complet** +
    un verdict SHA-bound. Retourne `{reviewed: bool, reason, verdict?, counts?}`. **Readiness-gate** (hold si
    tasks inachevées / feature jamais dispatchée / diff vide) ; **idempotent** (verdict déjà frais → skip) ;
    **best-effort** (reviewer échoué → pas de verdict → le gate de merge bloque proprement en aval)."""
    git = git or InternalGit()
    runner = runner or _default_runner
    project, feature = feature_ref.split("/", 1)
    feat = model.resolve_feature(conn, feature_ref)          # KeyError/ValueError si absent

    def _skip(reason: str) -> dict:                          # NON-RUN : trace best-effort + retour honnête
        _journal_non_run(conn, feature_ref, reason)
        return {"reviewed": False, "reason": reason}

    ok, reason = _readiness(conn, feat["id"])
    if not ok:
        return _skip(reason)

    sot = sot_path_for(settings, project)
    try:
        head_sha = git.feature_sha(sot, feat["branch"])
        diff_text = git.diff_text(sot, base=REVIEW_BASE, head=feat["branch"])
    except GitOpError:
        return _skip(f"branche {feat['branch']} absente — feature jamais dispatchée")
    if not diff_text.strip():
        return _skip("diff vide — rien à reviewer")
    if review.status(settings, project, feature, current_sha=head_sha)["fresh"]:
        return _skip("verdict Tier-1 déjà frais sur ce HEAD (idempotent)")

    wt = worktree_mod.worktree_path_for(settings, project, feature)
    if not wt.is_dir():
        return _skip(f"worktree absent : {wt} — la feature doit être vivante")

    tasks = list(conn.execute("SELECT slug, acceptance FROM tasks WHERE feature_id = ? ORDER BY slug",
                              (feat["id"],)))
    prompt = build_review_prompt(wt, feat, [dict(t) for t in tasks], diff_text)

    mcp_path = inject_mcp_config(wt, settings, slug=project)   # le reviewer peut vérifier un pattern (MCP)
    session_id = ids.new_id()
    argv = worker.build_headless_argv(session_id=session_id, work=False, mcp_config=mcp_path,
                                      output_format="stream-json")   # work=False → read-only (ne code pas)
    env = tools_env(settings)
    log_path = jobs.dispatch_log_path(settings, session_id)   # transcript suivable (plomberie du worker)
    # Bascule le worktree sur la facette `review` (settings.local.json READ-ONLY) : sans ça, la facette de
    # TRAVAIL (Write/Edit) resterait active → le reviewer pourrait coder (viole l'invariant génère/gate). Le
    # preflight ci-dessous lit cette settings.local.json → outils du reviewer, pas de l'ouvrier.
    facet_mod.activate_facet(wt, REVIEW_FACET)
    try:                                       # preflight = PRÉ-run → un échec ici est un non-run
        preflight_tools(wt, settings, env=env)
        auth.trust_workspace(sot_path_for(settings, project))
        if shutil.which("claude", path=env.get("PATH")) is None:
            raise ToolPreflightError("claude introuvable sur le PATH du reviewer — installe Claude Code.")
    except (ToolPreflightError, run.RunTimeout) as exc:
        return _skip(f"reviewer non lançable : {exc}")

    # Dé-squat : le run reviewer se journalise `kind='review'` (identité HONNÊTE, distincte de l'ouvrier),
    # ancré sur une task RÉELLE de la feature (FK requise ; `_readiness` garantit ≥1 task → jamais de drop
    # silencieux), et `record_start` est appelé AVANT le run pour que `started_at` soit le début réel : avant,
    # le code le posait APRÈS le run → 6 ms d'écart menteur, prouvé live sur 9123.
    anchor = conn.execute("SELECT id FROM tasks WHERE feature_id = ? ORDER BY slug LIMIT 1",
                          (feat["id"],)).fetchone()
    if anchor is None:                         # défensif (inatteignable après readiness) — jamais muet
        return _skip("feature sans task d'ancrage — run reviewer non journalisable comme job")
    job_id = jobs.record_start(conn, task_id=anchor["id"], worktree="(review)", session_id=session_id,
                               log_path=str(log_path), engine="reviewer-tier1", kind="review")
    started = time.monotonic()
    try:
        proc = runner(argv, cwd=wt, input_text=prompt, timeout=REVIEW_TIMEOUT, env=env)
    except run.RunTimeout as exc:              # le run a DÉMARRÉ puis timeout → job `failed`, pas non-run
        jobs.record_finish(conn, job_id, {"ok": False, "error": f"timeout {REVIEW_TIMEOUT:.0f}s : {exc}"},
                           wall_s=time.monotonic() - started, status="failed")
        return {"reviewed": False, "reason": f"reviewer non lançable : {exc}"}
    parsed = worker.parse_headless_result(proc.stdout, proc.returncode)
    jobs.record_finish(conn, job_id, parsed, wall_s=time.monotonic() - started)
    if not parsed.get("ok"):
        return {"reviewed": False, "reason": f"reviewer échoué : {parsed.get('error') or 'sortie illisible'}"}

    findings = _extract_findings(parsed.get("result"))
    verdict = review.write_verdict(settings, project, feature, {"findings": findings, "base": REVIEW_BASE},
                                   sha=head_sha, diff_text=diff_text)
    return {"reviewed": True, "reason": "verdict Tier-1 écrit", "verdict": verdict,
            "counts": verdict["counts"], "rejected": len(verdict["rejected"])}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit gate review-dispatch <feature>` : dispatche le reviewer Tier-1 (si prêt) et écrit le
    verdict SHA-bound. **Gate d'auth** (jamais de spawn silencieux d'un compte hérité)."""
    from cockpit.db import store

    if not auth.claude_auth_status()["authenticated"]:
        print(f"erreur : {auth.AUTH_HINT}")
        return 2
    conn = store.open_db(settings)
    try:
        report = dispatch_reviewer(conn, settings, feature_ref=args.feature)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    if not report["reviewed"]:
        print(f"reviewer non dispatché : {report['reason']}")
        return 1
    c = report["counts"]
    rej = f" — {report['rejected']} rejeté(s) (non citable)" if report.get("rejected") else ""
    print(f"verdict Tier-1 écrit : 🔴 {c['red']} · 🟡 {c['yellow']} · 🟣 {c['purple']}{rej}")
    return 0
