"""worker — spawn d'un worker `claude` headless (`claude -p`) **LOCAL** dans le worktree de la feature, sur
la NEXT task (séquentiel intra-feature). Gate d'entrée dur : **pas de task READY, pas de dispatch**.

Port de `services/aggregator/lib/worker_dispatch.py` (builders/parseurs **purs**) + le corps de
`dispatch_run.py`, **découpé** hors du monolithe 1650-LOC (#3) et **délocalisé** (#2) : le legacy
construisait un snippet shell `cd <wd> && claude …` livré base64 via `ssh dev@ip pct exec` ; ici on
construit un **argv liste** exécuté par `core.run` en local, `cwd=worktree`, **prompt sur stdin** (parade
E2BIG). Le transport (runner) est **injectable** → les tests ne spawnent jamais un vrai `claude`.

Constantes de politique portées **verbatim** (prouvées live void-runner 2026-06) : `acceptEdits` est
obligatoire pour écrire en headless ; l'allowlist code est large (toolchain), le bornage tient par le DENY
destructif (`rm`/`git push`/`git reset`/`sudo`, prime en tout mode).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from cockpit import auth
from cockpit.config import Settings
from cockpit.core import ids, run
from cockpit.db import store
from cockpit.dispatch import jobs, worktree
from cockpit.git.backend import GitBackend
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import InternalGit
from cockpit.projects.registry import get_project
from cockpit.provision.mcp import inject_mcp_config
from cockpit.roadmap import model, resolver
from cockpit.roadmap.prompt import build_worker_prompt
from cockpit.tools import ToolPreflightError, preflight_tools, tools_env

# -- politique d'outils (verbatim de worker_dispatch.py) --------------------------------------------
WRITE_PERMISSION_MODE = "acceptEdits"   # sans lui, `claude -p` refuse Write/Edit (aucun interlocuteur)
READONLY_TOOLS = "Read,Grep,Glob"
WRITE_CODE_TOOLS = "Bash,WebSearch,WebFetch"
DENY_DESTRUCTIVE = "Bash(rm *),Bash(git push *),Bash(git reset *),Bash(sudo *)"

DISPATCH_TIMEOUT = 1800.0   # s ; un worker qui pend ne bloque pas la forge (→ RunTimeout)

# runner(argv, *, cwd, input_text, timeout, env) -> RunResult. Défaut = subprocess local ; injecté en test.
Runner = Callable[..., run.RunResult]


def build_headless_argv(*, session_id: str, work: bool = True, model: str | None = None,
                        output_format: str = "json", mcp_config: Path | None = None) -> list[str]:
    """Argv de `claude -p` **local** (le prompt part sur le stdin, jamais l'argv). `--session-id` fixe le
    transcript à un chemin déterministe (suivi live). `work=True` → allowlist code + `acceptEdits` ;
    `work=False` → lecture seule (preuve de canal). Le DENY destructif est posé dans tous les cas. Si
    `mcp_config` est fourni, `--mcp-config <f>` charge le MCP de corpus injecté (non-strict : garde les autres
    configs). PUR."""
    argv = ["claude", "-p", "--output-format", output_format, "--session-id", session_id]
    if model:
        argv += ["--model", model]
    argv += ["--allowedTools", WRITE_CODE_TOOLS if work else READONLY_TOOLS]
    argv += ["--disallowedTools", DENY_DESTRUCTIVE]
    if work:
        argv += ["--permission-mode", WRITE_PERMISSION_MODE]
    if mcp_config is not None:
        argv += ["--mcp-config", str(mcp_config)]
    return argv


def _trailing_json_object(text: str) -> dict | None:
    """Extrait l'objet JSON FINAL d'un stdout précédé d'un préambule non-JSON (sentinelle de prep, bannière
    `bash -lc`…). `--output-format json` émet exactement un objet, en dernier. PUR (porté verbatim)."""
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj = json.loads(text[i:])
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                return obj
    return None


def parse_headless_result(stdout: str, returncode: int = 0) -> dict:
    """Normalise la sortie de `claude -p --output-format json`. PUR (porté verbatim). Fail-LOUD : rc≠0,
    sortie vide, JSON illisible, `is_error`/`api_error_status` → `ok=False` + `error` (jamais de faux-vert).
    Tolérant au préambule (objet FINAL). Retour : {ok, is_error, result, session_id, cost_usd, num_turns,
    error, raw}."""
    base = {"ok": False, "is_error": True, "result": None, "session_id": None,
            "cost_usd": None, "num_turns": None, "error": None, "raw": stdout}
    if returncode != 0:
        snippet = (stdout or "").strip()[:200] or "(vide)"
        return {**base, "error": f"claude -p rc={returncode} : {snippet}"}
    text = (stdout or "").strip()
    if not text:
        return {**base, "error": "sortie vide de claude -p"}
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        obj = _trailing_json_object(text)
        if obj is None:
            return {**base, "error": f"sortie non-JSON : {text[:200]}"}
    if not isinstance(obj, dict):
        return {**base, "error": f"JSON inattendu (pas un objet) : {text[:120]}"}
    is_error = bool(obj.get("is_error")) or (obj.get("api_error_status") not in (None, ""))
    err = None
    if is_error:
        err = obj.get("api_error_status") or obj.get("subtype") or "claude a signalé is_error"
    return {"ok": not is_error, "is_error": is_error, "result": obj.get("result"),
            "session_id": obj.get("session_id"), "cost_usd": obj.get("total_cost_usd"),
            "num_turns": obj.get("num_turns"), "error": err, "raw": stdout}


def _default_runner(argv: list[str], *, cwd: object, input_text: str, timeout: float,
                    env: Mapping[str, str] | None = None) -> run.RunResult:
    return run.run(argv, cwd=cwd, input_text=input_text, timeout=timeout, env=env)   # type: ignore[arg-type]


def dispatch_next(conn: sqlite3.Connection, settings: Settings, *, feature_ref: str,
                  git: GitBackend | None = None, runner: Runner | None = None) -> dict:
    """Dispatche un worker sur la NEXT task de `feature_ref` (`"projet/feature"`). Retourne un rapport
    `{dispatched: bool, reason, task?, job_id?, result?}`. **Gate no-task-no-dispatch** : refuse (sans
    spawn) si la feature n'a aucune task ou aucune task READY. Effets : réserve worktree+port, marque la
    task `in_progress`, spawn (runner injectable), journalise le job, révoque `in_progress`→`todo` si le run
    échoue (re-dispatchable)."""
    git = git or InternalGit()
    runner = runner or _default_runner
    project = feature_ref.split("/", 1)[0]

    index = resolver.index_for_feature(conn, feature_ref)   # KeyError si feature/projet absent
    if not index:
        return {"dispatched": False, "reason": "aucune task dans cette feature — pas de dispatch"}
    nxt = resolver.resolve_next(index)
    if nxt is None:
        counts = _counts(resolver.classify(index))
        return {"dispatched": False, "reason": f"aucune task READY ({counts}) — pas de dispatch"}

    feature = feature_ref.split("/", 1)[1]
    res = worktree.reserve(conn, settings, git, project=project, feature=feature)
    prompt = build_worker_prompt(get_project(conn, project), model.resolve_feature(conn, feature_ref),
                                 nxt, root=res["path"])

    session_id = ids.new_id()
    log_path = jobs.expected_transcript_path(session_id, res["path"])
    conn.execute("UPDATE tasks SET status = 'in_progress' WHERE id = ?", (nxt["id"],))
    conn.commit()
    job_id = jobs.record_start(conn, task_id=nxt["id"], worktree=str(res["path"]),
                               port=res["port"], session_id=session_id, log_path=str(log_path))

    # Câble le MCP de corpus dans le worktree (JWT minté, hors-git) → le worker « connaît ses outils ».
    # No-op honnête si le secret n'est pas configuré (install sans corpus privé) : le worker tourne sans MCP.
    mcp_path = inject_mcp_config(res["path"], settings, slug=project)
    argv = build_headless_argv(session_id=session_id, work=True, mcp_config=mcp_path)
    # PATH d'outils préfixé (`tools/bin`) → le worker RÉSOUT `codemap`/`docsmap`/`frontmap`/`node`/`ruff`…
    # que sa facette déclare (fin du `env=None` passif : le PATH systemd minimal ne les portait pas). Le MÊME
    # env sert au preflight (which) ET au spawn — cohérence garantie.
    env = tools_env(settings)
    started = time.monotonic()
    try:
        # Preflight fail-loud : tout binaire déclaré par la facette (allowedTools) doit résoudre AVANT le
        # spawn — sinon le worker le découvrirait absent à l'usage (échec tardif et opaque). Absent → job
        # échoué + task re-dispatchable (comme un RunTimeout), le runner n'est jamais appelé.
        preflight_tools(res["path"], settings, env=env)
        proc = runner(argv, cwd=res["path"], input_text=prompt, timeout=DISPATCH_TIMEOUT, env=env)
        parsed = parse_headless_result(proc.stdout, proc.returncode)
    except (ToolPreflightError, run.RunTimeout) as exc:
        parsed = {"ok": False, "session_id": session_id, "num_turns": None, "cost_usd": None,
                  "error": str(exc)}
    wall_s = time.monotonic() - started
    jobs.record_finish(conn, job_id, parsed, wall_s=wall_s)
    if parsed.get("ok"):
        # Le worker écrit le code mais NE fait PAS de git (mandat) → la forge committe son travail sur la
        # branche de feature dès le run réussi, pour que le gate SHA-bound ait un HEAD à ancrer. Arbre propre
        # (le worker n'a rien changé) → no-op propre (la feature reste alignée sur sa base).
        git.commit_worktree(res["path"], message=f"feat({feature}): {nxt['slug']} (worker dispatch)",
                            identity=resolve_identity(project, worktree.WORKTREE_BASE, role="worker"))
    else:
        conn.execute("UPDATE tasks SET status = 'todo' WHERE id = ?", (nxt["id"],))   # re-dispatchable
        conn.commit()
    return {"dispatched": True, "reason": "ok" if parsed.get("ok") else (parsed.get("error") or "échec"),
            "task": nxt["slug"], "job_id": job_id, "result": parsed}


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
    if not report["dispatched"]:
        print(report["reason"])
        return 1
    if report["result"].get("ok"):
        print(f"dispatch OK : {args.feature}/{report['task']} "
              f"(session {report['result'].get('session_id')}, job {report['job_id']})")
        return 0
    print(f"dispatch échoué : {report['reason']}")
    return 1
