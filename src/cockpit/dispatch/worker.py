"""worker — spawn d'un worker `claude` headless (`claude -p`) **LOCAL** dans le worktree de la feature, sur
la NEXT task (séquentiel intra-feature). Gate d'entrée dur : **pas de task READY, pas de dispatch**.

Port de `services/aggregator/lib/worker_dispatch.py` (builders/parseurs **purs**) + le corps de
`dispatch_run.py`, **découpé** hors du monolithe 1650-LOC (#3) et **délocalisé** (#2) : le legacy
construisait un snippet shell `cd <wd> && claude …` livré base64 via `ssh dev@ip pct exec` ; ici on
construit un **argv liste** exécuté par `core.run` en local, `cwd=worktree`, **prompt sur stdin** (parade
E2BIG). Le transport (runner) est **injectable** → les tests ne spawnent jamais un vrai `claude`.

Constantes de politique (base prouvée void-runner 2026-06, `rm` retiré du DENY 2026-07-22) : `acceptEdits`
requis pour écrire en headless ; l'allowlist code est large (toolchain), le bornage tient par le DENY
destructif (`git push`/`git reset`/`sudo`, prime en tout mode) + l'isolation du worktree jetable ; les `rm`
catastrophiques relèvent du circuit-breaker intrinsèque de `claude` (cf. `DENY_DESTRUCTIVE`).
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

from cockpit import auth
from cockpit.config import Settings
from cockpit.core import ids, run
from cockpit.db import store
from cockpit.dispatch import jobs, reconcile, worktree
from cockpit.git.backend import GitBackend
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import InternalGit
from cockpit.projects.registry import get_project, sot_path_for
from cockpit.provision.mcp import inject_mcp_config
from cockpit.roadmap import model, resolver
from cockpit.roadmap.prompt import build_fix_prompt, build_worker_prompt, findings_block
from cockpit.tools import ToolPreflightError, preflight_tools, tools_env

# -- politique d'outils (verbatim de worker_dispatch.py) --------------------------------------------
WRITE_PERMISSION_MODE = "acceptEdits"   # sans lui, `claude -p` refuse Write/Edit (aucun interlocuteur)
# Mode read-only du reviewer : en `-p` SANS `--permission-mode`, un outil hors-allowlist tombe en `ask` → sur
# un workspace TRUSTÉ (le reviewer appelle `trust_workspace`, le fallback « untrusted→deny » ne le protège
# PAS) il n'y a personne pour approuver → HANG jusqu'au timeout (stall silencieux de ~min observé sur
# void-runner). `dontAsk` auto-REFUSE tout ce qui prompterait (le modèle voit le deny et poursuit son jugement
# statique) et auto-PERMET les bash read-only (git diff/log/show — il SITUE le diff) : ajustement exact d'un
# reviewer read-only. Doc : permission-modes (« the session never waits for input »).
READONLY_PERMISSION_MODE = "dontAsk"
READONLY_TOOLS = "Read,Grep,Glob"       # baseline lecture-seule (preuve de canal) ; le reviewer l'ÉLARGIT
WRITE_CODE_TOOLS = "Bash,WebSearch,WebFetch"
# DENY destructif — deny PRIME sur allow (précédence deny>ask>allow), posé dans tous les modes. On borne les
# formes que le nommage rend fiables : push (remote), reset (réécriture d'historique), sudo (élévation). On NE
# borne PLUS `rm` : le blanket `Bash(rm *)` était du théâtre (contournable en 1 ligne Node) qui bloquait le
# nettoyage de scratch LÉGITIME (`rm _preview.tsx`) — un deny qui s'allume sur du normal est défaillant. Les
# `rm` catastrophiques sont couverts INTRINSÈQUEMENT par le circuit-breaker de `claude` (`rm -rf /` et
# `rm -rf ~` promptent toujours, même en bypass et même obfusqués `$(...)`) ; borner l'argument d'un `rm` par
# pattern est de toute façon fragile/incomplet (doc Claude Code). Le vrai bornage du repo DURABLE = SoT bare
# hors worktree + push/reset refusés ; le worktree lui-même est jetable (re-réservable).
DENY_DESTRUCTIVE = "Bash(git push *),Bash(git reset *),Bash(sudo *)"

DISPATCH_TIMEOUT = 1800.0   # s ; un worker qui pend ne bloque pas la forge (→ RunTimeout)

# Spin-guard : le timeout ci-dessus est le filet ULTIME (30 min). Un worker peut aussi NON-PROGRESSER sans
# pendre — enchaîner des tool-calls qui rendent la même sortie (observé void-runner : 12 sims plates à
# `win=0/50`, brute-force sans convergence). Ce n'est pas un stall (les événements coulent, `dontAsk` tient) ;
# aucun filet ne le coupait avant 1800s → burn silencieux. `make_spin_detector` le coupe tôt (→ RunSpin).
SPIN_WINDOW = 12   # tool-results consécutifs identiques (sans nouveauté d'assistant) → non-progression

# runner(argv, *, cwd, input_text, timeout, env) -> RunResult. Défaut = subprocess local ; injecté en test.
Runner = Callable[..., run.RunResult]


def make_spin_detector(window: int = SPIN_WINDOW) -> Callable[[str], str | None]:
    """Prédicat d'abort `on_line` (pour `run.run_streaming`) : détecte un worker qui n'AVANCE plus dans le
    flux stream-json, sans nouveau parseur (réutilise `jobs.normalize_line`). État en closure :
    - une fenêtre glissante des `window` derniers summaries de `tool_result` ;
    - tout TEXTE d'assistant NEUF réarme (vide la fenêtre) → une vraie progression (raisonnement neuf entre
      deux outils) ne déclenche jamais.
    Rend une raison ssi la fenêtre est PLEINE et ses `window` summaries sont identiques et NON vides ; le
    cœur tue alors le process (→ `RunSpin`). Conservateur par conception : faux-positifs quasi-nuls ; le
    `DISPATCH_TIMEOUT` reste le filet ultime pour les spins « pensés à voix haute » (texte neuf par tour)."""
    results: deque[str] = deque(maxlen=window)
    last_text: list[str] = []   # dernier texte d'assistant vu (liste = cellule mutable en closure)

    def _detect(line: str) -> str | None:
        ev = jobs.normalize_line(line)
        if ev is None:
            return None
        if ev.get("type") == "assistant":
            text = (ev.get("text") or "").strip()
            if text and text != (last_text[0] if last_text else None):
                last_text[:] = [text]
                results.clear()   # progression réelle → réarme le compteur
            return None
        if ev.get("type") == "tool_result":
            summary = " | ".join((r.get("summary") or "") for r in ev.get("results", []))
            results.append(summary)
            if len(results) == window and summary and all(s == summary for s in results):
                return (f"spin: aucune nouveauté sur {window} tool-results consécutifs "
                        "(worker répète la même sortie)")
        return None

    return _detect


def build_headless_argv(*, session_id: str, work: bool = True, model: str | None = None,
                        output_format: str = "json", mcp_config: Path | None = None,
                        allowed_tools: str | None = None) -> list[str]:
    """Argv de `claude -p` **local** (le prompt part sur le stdin, jamais l'argv). `--session-id` fixe le
    transcript à un chemin déterministe (suivi live). `work=True` → allowlist code + `--permission-mode
    acceptEdits` ; `work=False` → read-only + `--permission-mode dontAsk` (auto-refuse sans HANG tout outil
    hors-allowlist). `allowed_tools`, si fourni, **override** le choix `WRITE_CODE_TOOLS`/`READONLY_TOOLS` (le
    reviewer y injecte son allowlist read-only ÉLARGIE : git-lecture + maps, cf. `reviewer.REVIEW_TOOLS`). Le
    DENY destructif est posé dans tous les cas et **prime** sur l'allow (précédence deny>ask>allow). Si
    `mcp_config` est fourni, `--mcp-config <f>` charge le MCP de corpus injecté (non-strict : garde les autres
    configs). PUR."""
    argv = ["claude", "-p", "--output-format", output_format, "--session-id", session_id]
    if output_format == "stream-json":
        argv += ["--verbose"]            # `claude -p --output-format stream-json` EXIGE --verbose
    if model:
        argv += ["--model", model]
    argv += ["--allowedTools", allowed_tools or (WRITE_CODE_TOOLS if work else READONLY_TOOLS)]
    argv += ["--disallowedTools", DENY_DESTRUCTIVE]
    # `--permission-mode` posé dans TOUS les cas (headless) : `acceptEdits` pour l'ouvrier
    # (auto-accepte Write/Edit), `dontAsk` pour le reviewer read-only (auto-refuse tout ce qui prompterait au
    # lieu de HANG en `ask`). Sans lui, un outil hors-allowlist bloque la session jusqu'au timeout.
    argv += ["--permission-mode", WRITE_PERMISSION_MODE if work else READONLY_PERMISSION_MODE]
    if mcp_config is not None:
        argv += ["--mcp-config", str(mcp_config)]
    return argv


# Parseur du résultat `claude -p` : relocalisé dans `jobs.py` (foyer avec `normalize_line`, sibling pur ;
# rompt le cycle d'import `reconcile→worker` — la réconciliation d'orphelin en a besoin). Ré-exporté ici
# pour les appelants historiques (`worker.parse_headless_result` : ce module, `reviewer`, tests).
parse_headless_result = jobs.parse_headless_result


def write_decision_doc(worktree: Path, task_slug: str, result: str | None, *,
                       date_str: str, preamble: str | None = None) -> Path | None:
    """Persiste le message final du worker (`result`) en **minerai local** durable :
    `<worktree>/docs/decisions/<date_str>--<task_slug>.md`, corps **verbatim** (le worker le termine par une
    section `## Décisions prises`, cf. `prompt._mandate`). Provenance portée par le NOM (date+slug) +
    l'auteur git du commit — pas de frontmatter neuf. **No-op** (retourne `None`, n'écrit rien) si `result`
    est absent ou blanc : pas de doc vide, pas de minerai orphelin. PUR (date injectée → testable sans
    horloge). L'appelant ne l'invoque que dans la branche run-réussi → jamais de trace sur un run raté.

    `preamble` (optionnel) est PRÉFIXÉ au corps (`preamble\\n\\n{result}`) : la passe de fix y injecte les
    findings du gate rouge d'origine (`findings_block`), pour que le minerai capture findings→cause→fix, pas
    seulement le récit du worker. La garde no-op reste sur `result` seul — un preamble sans corps ne fabrique
    jamais un minerai (symétrie avec le run raté)."""
    if not result or not result.strip():
        return None
    body = f"{preamble.rstrip()}\n\n{result}" if preamble else result
    doc = Path(worktree) / "docs" / "decisions" / f"{date_str}--{task_slug}.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    return doc


def _make_default_runner(out_path: str, conn: sqlite3.Connection | None = None,
                         job_id: str | None = None) -> Runner:
    """Runner par défaut du dispatch : exécute `claude -p` en **streamant** son stdout (`stream-json`) dans
    `out_path` au fil de l'eau → le transcript est suivable EN DIRECT (le pont `dispatch/stream` tail ce
    fichier), au lieu de n'apparaître qu'à la fin. `out_path` (le `log_path` du job) est capturé ici → le
    protocole `Runner` reste inchangé, les runners injectés en test ne le voient pas.

    Spawn en **session propre** (`new_session=True` → le worker est leader de groupe, tuable en groupe par
    l'abort) et **persiste son pid** dès le spawn (`on_spawn` → `jobs.record_pid`) sur la connexion
    thread-locale `conn` : c'est le handle explicite que `cockpit abort` lit en DB (fin du `pkill` fragile).
    `conn`/`job_id` absents (runner de test) → pas de persistance, comportement inchangé.

    Câble aussi le **spin-guard** (`on_line=make_spin_detector()`) : un worker qui n'avance plus est coupé tôt
    (→ `RunSpin`) au lieu de brûler jusqu'au timeout. Détecteur FRAIS par run (état par-worker). Les runners
    injectés en test ne passent pas par ici → pas de détecteur, comportement inchangé."""
    def _runner(argv: list[str], *, cwd: object, input_text: str, timeout: float,
                env: Mapping[str, str] | None = None) -> run.RunResult:
        on_spawn = ((lambda pid: jobs.record_pid(conn, job_id, pid))
                    if conn is not None and job_id is not None else None)
        return run.run_streaming(argv, cwd=cwd, input_text=input_text, timeout=timeout,   # type: ignore[arg-type]
                                 env=env, out_path=out_path, new_session=True, on_spawn=on_spawn,
                                 on_line=make_spin_detector())
    return _runner


def dispatch_next(conn: sqlite3.Connection, settings: Settings, *, feature_ref: str,
                  git: GitBackend | None = None, runner: Runner | None = None) -> dict:
    """Dispatche un worker sur la NEXT task de `feature_ref` (`"projet/feature"`). Retourne un rapport
    `{dispatched: bool, reason, task?, job_id?, result?}`. **Gate no-task-no-dispatch** : refuse (sans
    spawn) si la feature n'a aucune task ou aucune task READY. Effets : réserve worktree+port, marque la
    task `in_progress`, spawn (runner injectable), journalise le job, révoque `in_progress`→`todo` si le run
    échoue (re-dispatchable)."""
    git = git or InternalGit()
    project = feature_ref.split("/", 1)[0]

    index = resolver.index_for_feature(conn, feature_ref)   # KeyError si feature/projet absent
    if not index:
        return {"dispatched": False, "reason": "aucune task dans cette feature — pas de dispatch"}
    nxt = resolver.resolve_next(index)
    if nxt is None:
        counts = _counts(resolver.classify(index))
        return {"dispatched": False, "reason": f"aucune task READY ({counts}) — pas de dispatch"}

    # Fork headless↔interactif (v12) : une task `interactive` (interview/cadrage) NE peut PAS se mener en
    # `claude -p` (un headless n'interviewe personne). Le branchement appartient au DISPATCH, pas au worker :
    # on refuse le spawn AVANT tout reserve, la task reste `todo` (jamais `in_progress` ni faux-`done`), et on
    # SURFACE `needs_terminal` → l'humain la mène via `cockpit interview <projet>` (terminal interactif).
    if nxt.get("mode") == "interactive":
        return {"dispatched": False, "reason": "interactive", "needs_terminal": True,
                "task": nxt["slug"], "feature_ref": feature_ref}

    feature = feature_ref.split("/", 1)[1]
    res = worktree.reserve(conn, settings, git, project=project, feature=feature)
    prompt = build_worker_prompt(get_project(conn, project), model.resolve_feature(conn, feature_ref),
                                 nxt, root=res["path"])

    session_id = ids.new_id()
    # log_path = le fichier où le daemon STREAME le stdout stream-json du worker (suivi live), sous
    # `home/logs/`. Distinct du transcript de session que `claude` écrit sous `~/.claude/projects/…` (ne pas
    # l'écraser) : notre flux stdout porte déjà tous les événements (assistant/tool_result/result).
    log_path = jobs.dispatch_log_path(settings, session_id)
    conn.execute("UPDATE tasks SET status = 'in_progress' WHERE id = ?", (nxt["id"],))
    conn.commit()
    job_id = jobs.record_start(conn, task_id=nxt["id"], worktree=str(res["path"]),
                               port=res["port"], session_id=session_id, log_path=str(log_path))

    def _on_success(parsed: dict) -> None:
        # Récolte le minerai AVANT le commit : le message final du worker (ses décisions) devient un
        # `docs/decisions/<date>--<task>.md` durable, embarqué dans le même commit que le code. Branche ok
        # uniquement → un run raté (revient `todo`) ne laisse jamais de minerai orphelin. Le worker écrit le
        # code mais NE fait PAS de git (mandat) → la forge committe son travail sur la branche de feature dès
        # le run réussi, pour que le gate SHA-bound ait un HEAD à ancrer (arbre net → no-op propre).
        write_decision_doc(res["path"], nxt["slug"], parsed.get("result"),
                           date_str=date.today().isoformat())
        git.commit_worktree(res["path"], message=f"feat({feature}): {nxt['slug']} (worker dispatch)",
                            identity=resolve_identity(project, worktree.WORKTREE_BASE, role="worker"))

    def _on_failure(_parsed: dict) -> None:
        conn.execute("UPDATE tasks SET status = 'todo' WHERE id = ?", (nxt["id"],))   # re-dispatchable
        conn.commit()

    parsed = _run_worker(conn, settings, res=res, session_id=session_id, job_id=job_id, log_path=log_path,
                         prompt=prompt, project=project, runner=runner,
                         on_success=_on_success, on_failure=_on_failure)
    return {"dispatched": True, "reason": "ok" if parsed.get("ok") else (parsed.get("error") or "échec"),
            "task": nxt["slug"], "job_id": job_id, "result": parsed}


def _run_worker(conn: sqlite3.Connection, settings: Settings, *, res: dict, session_id: str, job_id: str,
                log_path: Path, prompt: str, project: str, runner: Runner | None,
                on_success: Callable[[dict], None], on_failure: Callable[[dict], None]) -> dict:
    """Tronc de spawn PARTAGÉ par `dispatch_next` (task) et `dispatch_fix` (correction) : câble le MCP,
    construit l'argv headless, sélectionne le runner (défaut = streaming vers `log_path`),
    preflight/trust/check `claude`, spawne, parse, `record_finish`. `on_success(parsed)`/`on_failure(parsed)`
    portent les effets SPÉCIFIQUES à l'appelant (récolte + commit + transition de task, ou commit de fix) —
    appelés DANS la garde anti-orphelin (sémantique identique à l'ancien inline). Retourne `parsed`.

    Garde de finalisation : le job est déjà `running` (record_start côté appelant). SEUL `record_finish` (ou
    le callback d'échec) le sort de cet état. Toute exception qui échappe (preflight non-ToolPreflightError,
    trust/DB, spawn OSError, commit forge…) sauterait la sortie → job `running` ZOMBIE (daemon vivant). On
    finalise alors l'orphelin (killed + task `in_progress`→todo, scopé au job encore `running`) PUIS on
    re-propage LOUD — jamais avalé, jamais laissé zombie."""
    # Câble le MCP de corpus dans le worktree (JWT minté, hors-git) → le worker « connaît ses outils ».
    # No-op honnête si le secret n'est pas configuré (install sans corpus privé) : le worker tourne sans MCP.
    mcp_path = inject_mcp_config(res["path"], settings, slug=project)
    # `stream-json` : le worker émet ses événements ligne-à-ligne (NDJSON) sur stdout → streamés vers log_path
    # en direct. Le runner par défaut streame ; un runner injecté (test) reçoit le même argv sans streamer.
    argv = build_headless_argv(session_id=session_id, work=True, mcp_config=mcp_path,
                               output_format="stream-json")
    active_runner = runner or _make_default_runner(str(log_path), conn, job_id)
    # PATH d'outils préfixé (`tools/bin`) → le worker RÉSOUT `codemap`/`docsmap`/`frontmap`/`node`/`ruff`…
    # que sa facette déclare. Le MÊME env sert au preflight (which) ET au spawn — cohérence garantie.
    env = tools_env(settings)
    started = time.monotonic()
    try:
        try:
            # Preflight fail-loud : tout binaire déclaré par la facette (allowedTools) doit résoudre AVANT le
            # spawn — sinon le worker le découvrirait absent à l'usage (échec tardif et opaque).
            preflight_tools(res["path"], settings, env=env)
            # Le worker n'exécute ses outils QUE si son workspace est TRUSTED — la clé de confiance du
            # worktree est le SoT bare du projet → marqué trusted avant le spawn (idempotent).
            auth.trust_workspace(sot_path_for(settings, project))
            # `claude` (moteur du worker) doit résoudre dans l'env du worker (`tools_env` a `~/.local/bin`) :
            # absent → FAIL-LOUD (comme le preflight) au lieu d'un spawn mort-né silencieux.
            if shutil.which("claude", path=env.get("PATH")) is None:
                raise ToolPreflightError(
                    "claude introuvable sur le PATH du worker — installe Claude Code "
                    "(`provision-ct.sh --with-claude`, ou `claude` dans ~/.local/bin) puis relance.")
            proc = active_runner(argv, cwd=res["path"], input_text=prompt, timeout=DISPATCH_TIMEOUT, env=env)
            parsed = parse_headless_result(proc.stdout, proc.returncode)
        except (ToolPreflightError, run.RunTimeout) as exc:
            parsed = {"ok": False, "session_id": session_id, "num_turns": None, "cost_usd": None,
                      "error": str(exc)}
        wall_s = time.monotonic() - started
        jobs.record_finish(conn, job_id, parsed, wall_s=wall_s)
        if parsed.get("ok"):
            on_success(parsed)
        else:
            on_failure(parsed)
        return parsed
    except BaseException:
        reconcile.mark_job_orphan(conn, job_id)   # jamais de zombie ; no-op si le job est déjà finalisé
        raise


def dispatch_fix(conn: sqlite3.Connection, settings: Settings, *, feature_ref: str, findings: dict,
                 git: GitBackend | None = None, runner: Runner | None = None) -> dict:
    """Dispatche un worker de **correction** sur la branche de feature (gate rouge → refix) : réutilise le
    worktree (rebase idempotent sur `dev` — « retour au dev »), construit le brief depuis les `findings` (le
    brief = le verdict), spawne, RÉCOLTE le minerai puis committe le fix. PAS de task → aucune transition
    `todo`/`done` ; mais la correction distille son savoir (findings→cause→fix) en `docs/decisions/` comme
    une passe de task (le fix porte souvent le savoir le plus précieux — cause racine, contournement écarté).
    Job journalisé `kind='fix'` (ancré sur une task `done` de la feature pour la découverte live/`list_jobs`)
    → la borne se compte par `count_fix_jobs`. Retourne `{dispatched, reason, job_id?, result?}`."""
    git = git or InternalGit()
    project, feature = feature_ref.split("/", 1)
    feat = model.resolve_feature(conn, feature_ref)
    anchor = _feature_anchor_task_id(conn, feat["id"])
    if anchor is None:
        return {"dispatched": False, "reason": "aucune task dans cette feature — rien à corriger"}
    res = worktree.reserve(conn, settings, git, project=project, feature=feature)
    prompt = build_fix_prompt(get_project(conn, project), feat, findings=findings, root=res["path"])
    session_id = ids.new_id()
    log_path = jobs.dispatch_log_path(settings, session_id)
    job_id = jobs.record_start(conn, task_id=anchor, worktree=str(res["path"]), port=res["port"],
                               session_id=session_id, log_path=str(log_path), kind="fix")

    def _on_success(parsed: dict) -> None:
        # Récolte le minerai de la correction AVANT le commit, symétrique de `dispatch_next._on_success` : le
        # message final du worker (cause racine + fix retenu) devient un `docs/decisions/<date>--<feature>-
        # refix-<job>.md`, PRÉFIXÉ des findings du gate rouge d'origine → le minerai capture findings→cause→
        # fix (self-contained ; les verdicts de gate sont purgés au merge). `job_id` discrimine plusieurs
        # refix le même jour (aucun écrasement). No-op honnête si le worker n'a rien distillé (garde sur
        # `result`). Puis la forge committe la correction (le worker NE fait PAS de git — mandat) → le gate
        # SHA-bound s'ancre sur le nouveau HEAD (re-finalisé par `refix`).
        preamble = (f"# Passe de correction — feature {feature}\n\n"
                    f"## Bloqueurs du gate rouge (findings d'origine)\n{findings_block(findings)}")
        write_decision_doc(res["path"], f"{feature}-refix-{job_id}", parsed.get("result"),
                           date_str=date.today().isoformat(), preamble=preamble)
        git.commit_worktree(res["path"], message=f"fix({feature}): gate refix pass (worker dispatch)",
                            identity=resolve_identity(project, worktree.WORKTREE_BASE, role="worker"))

    parsed = _run_worker(conn, settings, res=res, session_id=session_id, job_id=job_id, log_path=log_path,
                         prompt=prompt, project=project, runner=runner,
                         on_success=_on_success, on_failure=lambda _p: None)
    return {"dispatched": True, "reason": "ok" if parsed.get("ok") else (parsed.get("error") or "échec"),
            "job_id": job_id, "result": parsed}


def _feature_anchor_task_id(conn: sqlite3.Connection, feature_id: str) -> str | None:
    """Une task « ancre » de la feature pour rattacher un job de correction (le modèle `dispatch_jobs` exige
    un `task_id` NON NULL, or un fix corrige la feature ENTIÈRE, pas une task) : une task `done` de préférence
    (le fix corrige du travail landé), sinon n'importe laquelle. None si la feature n'a aucune task. Le CHOIX
    n'affecte que le `task_slug` affiché — la borne `count_fix_jobs` est feature-scopée (join tasks)."""
    row = conn.execute(
        "SELECT id FROM tasks WHERE feature_id = ? ORDER BY (status = 'done') DESC, id DESC LIMIT 1",
        (feature_id,)).fetchone()
    return row["id"] if row else None


def _counts(classified: dict[str, dict]) -> str:
    tally: dict[str, int] = {}
    for t in classified.values():
        tally[t["state"]] = tally.get(t["state"], 0) + 1
    return ", ".join(f"{k}:{v}" for k, v in sorted(tally.items()))


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit dispatch <feature>` : résout la NEXT task, refuse si aucune (gate anti-dispatch),
    réserve le worktree, spawn `claude -p` local. **Gate d'auth** : refuse AVANT tout spawn si la machine
    n'a pas d'auth Claude explicite (jamais d'usage silencieux d'un compte hérité)."""
    if not auth.claude_auth_status()["authenticated"]:
        print(f"erreur : {auth.AUTH_HINT}")
        return 2
    conn = store.open_db(settings)
    try:
        report = dispatch_next(conn, settings, feature_ref=args.feature)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    if report.get("needs_terminal"):
        project = args.feature.split("/", 1)[0]
        print(f"🖐 {args.feature}/{report['task']} — interview terminale requise (task interactive) : "
              f"lance `cockpit interview {project}` dans un terminal.")
        return 0                                    # état tenu (pas un échec) : attend l'humain au terminal
    if not report["dispatched"]:
        print(report["reason"])
        return 1
    if report["result"].get("ok"):
        print(f"dispatch OK : {args.feature}/{report['task']} "
              f"(session {report['result'].get('session_id')}, job {report['job_id']})")
        return 0
    print(f"dispatch échoué : {report['reason']}")
    return 1
