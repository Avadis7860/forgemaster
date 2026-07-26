"""jobs — état des runs worker (table `dispatch_jobs`) + suivi robuste des logs. Un job matérialise un
worker sur une task : session, port, statut, métriques, transcript streamable en live (web P5).

Deux concerns :
- **état** : `record_start` (job `running`) / `record_finish` (statut `done|failed` + métriques du run).
- **suivi** (refactor **#5**) : le legacy suivait le transcript par un one-liner `find … | tail -F` (poll
  120×1s, sur ssh) — fragile. Ici, lecture **incrémentale locale** par `(inode, offset)` : `read_events`
  reprend là où on s'est arrêté, relit tout si l'inode change (rotation), ignore une ligne partielle en
  cours d'écriture. Le normaliseur `normalize_line` (transcript JSONL → événement conversation) est **porté
  verbatim** de `services/aggregator/transcript_norm.py` (pur, réutilisé live ET relecture).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from cockpit.core import ids

if TYPE_CHECKING:
    from cockpit.config import Settings

DISPATCH_ENGINE = "worker-dispatch-v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


# -- état des jobs ----------------------------------------------------------------------------------

def record_start(conn: sqlite3.Connection, *, task_id: str, worktree: str, session_id: str,
                 port: int | None = None, pid: int | None = None, log_path: str | None = None,
                 engine: str = DISPATCH_ENGINE, kind: str = "task") -> str:
    """Insère un `dispatch_job` en statut `running`, retourne son id. `session_id` = le handle de suivi
    live (le chemin du transcript en dérive). `kind` (v11) = identité du run (`task` ouvrier par défaut ;
    le reviewer pose `review`) — à appeler AVANT le run pour que `started_at` soit le début réel."""
    job_id = ids.new_id()
    conn.execute(
        "INSERT INTO dispatch_jobs (id, task_id, worktree_path, port, pid, status, kind, log_path, "
        "session_id, engine, started_at) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)",
        (job_id, task_id, worktree, port, pid, kind, log_path, session_id, engine, _now()))
    conn.commit()
    return job_id


def record_pid(conn: sqlite3.Connection, job_id: str, pid: int) -> None:
    """Renseigne le `pid` (== pgid, le worker est spawné `start_new_session`) d'un job `running` — le handle
    explicite que l'abort lit pour tuer le worker en groupe (`os.killpg`), au lieu d'un `pgrep` fragile.
    Appelé par le runner par défaut juste après le spawn (`run_streaming(on_spawn=…)`). Le WHERE
    `status='running'` évite d'écrire un pid sur un job déjà finalisé (course rare mais possible)."""
    conn.execute(
        "UPDATE dispatch_jobs SET pid = ? WHERE id = ? AND status = 'running'", (pid, job_id))
    conn.commit()


def record_finish(conn: sqlite3.Connection, job_id: str, parsed: dict, *,
                  wall_s: float | None = None, status: str | None = None) -> None:
    """Clôt un job : statut `done` si `parsed.ok`, sinon `failed` (un échec se journalise aussi) ; un
    `status` explicite l'emporte (`killed` — cf. CHECK `dispatch_jobs.status`). Renseigne les métriques du
    run (num_turns, coût) et la raison d'échec `error` (v11 : le snippet calculé par `parse_headless_result`,
    persisté au lieu d'être jeté dans le retour HTTP). La garde `AND status='running'` est **essentielle** :
    si un abort concurrent a déjà posé `killed` (course `record_finish` vs abort), on ne le **clobbe pas** en
    `failed` — un job déjà finalisé ne se re-finalise jamais. L'usage token (v13 : input/output/cache + modèle
    dominant, extrait par `parse_headless_result`) est persisté ici — NULL sur un run raté (parsed sans
    usage), jamais un faux zéro. Le $ reste `cost_usd` (= total_cost_usd de Claude), non recalculé. Un run
    **rate-limité** (`parsed.rate_limited`, v15) est classé `rate_limited` — distinct de `failed` : le plafond
    5 h de l'org rejette `claude -p`, ce n'est pas un échec de la task (l'orchestrateur TIENT la feature)."""
    final = status or ("rate_limited" if parsed.get("rate_limited")
                       else "done" if parsed.get("ok") else "failed")
    conn.execute(
        "UPDATE dispatch_jobs SET status = ?, num_turns = ?, cost_usd = ?, wall_s = ?, "
        "input_tokens = ?, output_tokens = ?, cache_read_tokens = ?, cache_creation_tokens = ?, model = ?, "
        "session_id = COALESCE(?, session_id), error = ?, ended_at = ? WHERE id = ? AND status = 'running'",
        (final, parsed.get("num_turns"), parsed.get("cost_usd"), wall_s,
         parsed.get("input_tokens"), parsed.get("output_tokens"), parsed.get("cache_read_tokens"),
         parsed.get("cache_creation_tokens"), parsed.get("model"),
         parsed.get("session_id"), parsed.get("error"), _now(), job_id))
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict:
    """Un job par id, ou `KeyError`."""
    r = conn.execute("SELECT * FROM dispatch_jobs WHERE id = ?", (job_id,)).fetchone()
    if r is None:
        raise KeyError(job_id)
    return dict(r)


def count_fix_jobs(conn: sqlite3.Connection, feature_id: str) -> int:
    """Nombre de jobs de **correction** (`kind='fix'`) déjà lancés pour une feature (join `tasks`). Sert la
    **borne** de `dispatch.refix` (max passes de fix/feature) — feature-scopée, indépendante de la task-ancre
    à laquelle chaque fix est rattaché."""
    r = conn.execute(
        "SELECT COUNT(*) AS n FROM dispatch_jobs j JOIN tasks t ON j.task_id = t.id "
        "WHERE t.feature_id = ? AND j.kind = 'fix'", (feature_id,)).fetchone()
    return int(r["n"]) if r else 0


def list_jobs(conn: sqlite3.Connection, feature_id: str) -> list[dict]:
    """Jobs d'une feature (join `tasks`), du plus récent au plus ancien ; `task_slug` joint pour l'affichage.
    Sert la **découverte** du job à streamer (le POST dispatch bloque jusqu'à la fin → le front trouve le job
    en cours par cette liste) et l'**historique** des runs. Tri `started_at` DESC (ISO → lexical = réel)."""
    rows = conn.execute(
        "SELECT j.*, t.slug AS task_slug FROM dispatch_jobs j JOIN tasks t ON j.task_id = t.id "
        "WHERE t.feature_id = ? ORDER BY j.started_at DESC, j.id DESC", (feature_id,)).fetchall()
    return [dict(r) for r in rows]


# -- résolution du transcript + lecture incrémentale (#5) -------------------------------------------

def dispatch_log_path(settings: Settings, session_id: str) -> Path:
    """Chemin du log **streamé** d'un dispatch : `<home>/logs/<session_id>.jsonl`. Le daemon y écrit le stdout
    stream-json du worker au fil de l'eau (suivi live via `dispatch/stream`) → distinct du transcript de
    session que `claude` écrit sous `~/.claude/projects/…` (jamais écrasé). Crée `logs/` au besoin."""
    logs = settings.logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    return logs / f"{session_id}.jsonl"


def expected_transcript_path(session_id: str, cwd: str | Path, *, home: Path | None = None) -> Path:
    """Chemin **déterministe** attendu du transcript : `~/.claude/projects/<encode-cwd>/<session_id>.jsonl`.
    L'encodage du cwd par Claude Code remplace `/`, `.` et `_` par `-`. Calculé AVANT le spawn (le worker
    créera le fichier) → stocké en `log_path`. Aucune vérification d'existence."""
    base = (home or Path.home()) / ".claude" / "projects"
    encoded = "".join("-" if c in "/._" else c for c in str(Path(cwd).resolve()))
    return base / encoded / f"{session_id}.jsonl"


def transcript_path(session_id: str, cwd: str | Path, *, home: Path | None = None) -> Path | None:
    """Chemin **réel** du transcript : le chemin déterministe s'il existe, sinon un glob de secours (une
    seule passe) si l'encodage exact varie. None si introuvable (le worker n'a encore rien écrit)."""
    direct = expected_transcript_path(session_id, cwd, home=home)
    if direct.is_file():
        return direct
    base = (home or Path.home()) / ".claude" / "projects"
    if base.is_dir():
        for hit in base.glob(f"*/{session_id}.jsonl"):
            return hit
    return None


def read_events(path: str | Path, *, offset: int = 0, inode: int | None = None) -> dict:
    """Lecture **incrémentale** du transcript : retourne `{events, offset, inode}`. Reprend à `offset` ;
    si l'`inode` a changé (rotation), relit depuis 0 ; s'arrête sur une ligne partielle (écriture en cours)
    sans consommer son offset — elle sera relue au prochain appel. Robuste, sans sous-shell `tail -F`."""
    p = Path(path)
    if not p.exists():
        return {"events": [], "offset": offset, "inode": inode}
    cur_ino = p.stat().st_ino
    start = 0 if (inode is not None and cur_ino != inode) else offset
    with p.open("rb") as f:
        f.seek(start)
        data = f.read()
    consumed = 0
    events: list[dict] = []
    for line in data.decode("utf-8", "replace").splitlines(keepends=True):
        if not line.endswith("\n"):
            break   # ligne partielle → on ne l'avale pas (relue quand elle sera complète)
        consumed += len(line.encode("utf-8"))
        ev = normalize_line(line)
        if ev is not None:
            events.append(ev)
    return {"events": events, "offset": start + consumed, "inode": cur_ino}


def tail(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    """Événements conversationnels normalisés du transcript d'un job (lecture one-shot de tout le courant ;
    le daemon boucle `read_events` avec l'offset persisté pour le live). Vide si le transcript est absent."""
    job = get_job(conn, job_id)
    if not job.get("log_path"):
        return []
    return read_events(job["log_path"])["events"]


# -- normaliseur transcript (porté verbatim de transcript_norm.py, PUR) -----------------------------

_INPUT_SUMMARY_MAX = 200
_RESULT_SUMMARY_MAX = 240

# Champ d'`input` le plus parlant par outil (résumé court d'un tool_use). Défaut : 1ʳᵉ valeur scalaire.
_TOOL_INPUT_FIELD = {
    "Bash": "command", "Read": "file_path", "Write": "file_path", "Edit": "file_path",
    "NotebookEdit": "notebook_path", "Glob": "pattern", "Grep": "pattern",
    "Task": "description", "Agent": "description", "WebSearch": "query", "WebFetch": "url",
}


def _truncate(s: object, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _summarize_input(name: str | None, inp: object) -> str:
    """Résumé court de l'`input` d'un tool_use (champ parlant par outil, sinon 1ʳᵉ valeur). PUR."""
    if not isinstance(inp, dict) or not inp:
        return ""
    field = _TOOL_INPUT_FIELD.get(name or "")
    if field and inp.get(field) is not None:
        return _truncate(inp[field], _INPUT_SUMMARY_MAX)
    for v in inp.values():
        if isinstance(v, (str, int, float)):
            return _truncate(v, _INPUT_SUMMARY_MAX)
    return _truncate(",".join(inp.keys()), _INPUT_SUMMARY_MAX)


def _summarize_result(content: object) -> str:
    """Résumé court d'un tool_result (`content` = str OU liste de blocs `{type:text,text}`). PUR."""
    if isinstance(content, str):
        return _truncate(content, _RESULT_SUMMARY_MAX)
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return _truncate(" ".join(p for p in parts if p), _RESULT_SUMMARY_MAX)
    return ""


def _usage(usage: object) -> dict | None:
    """Extrait `{output_tokens, web_search_requests}` de `message.usage`. PUR. None si rien d'exploitable."""
    if not isinstance(usage, dict):
        return None
    out: dict = {}
    if isinstance(usage.get("output_tokens"), int):
        out["output_tokens"] = usage["output_tokens"]
    stu = usage.get("server_tool_use")
    if isinstance(stu, dict) and isinstance(stu.get("web_search_requests"), int):
        out["web_search_requests"] = stu["web_search_requests"]
    return out or None


def normalize_line(line: str) -> dict | None:
    """Une ligne JSONL du transcript → événement conversation canonique, ou None si non affichable. PUR.
    Ignoré : lignes non-JSON, types non-conversationnels, assistant/user vides, et le PROMPT user initial."""
    try:
        o = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(o, dict):
        return None
    t = o.get("type")
    ts = o.get("timestamp")
    raw_msg = o.get("message")
    msg: dict = raw_msg if isinstance(raw_msg, dict) else {}
    content = msg.get("content")

    if t == "assistant" and isinstance(content, list):
        texts, tools = [], []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and (b.get("text") or "").strip():
                texts.append(b["text"])
            elif b.get("type") == "tool_use":
                tools.append({"name": b.get("name"),
                              "input_summary": _summarize_input(b.get("name"), b.get("input"))})
        usage = _usage(msg.get("usage"))
        if not texts and not tools and not usage:
            return None
        ev: dict = {"type": "assistant", "ts": ts}
        if texts:
            ev["text"] = "\n\n".join(texts)
        if tools:
            ev["tools"] = tools
        if usage:
            ev["usage"] = usage
        return ev

    if t == "user" and isinstance(content, list):
        results = [{"ok": not bool(b.get("is_error")), "summary": _summarize_result(b.get("content"))}
                   for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        if results:
            return {"type": "tool_result", "ts": ts, "results": results}
        return None

    return None


# -- parseur du résultat `claude -p` (PUR ; foyer avec `normalize_line`, sibling pur ; consommé par
#    `record_finish` ET par la réconciliation d'orphelin `reconcile._terminal_result`) -----------------

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


def _result_event(text: str) -> dict | None:
    """L'objet-résultat de `claude -p`. `--output-format json` émet **un seul** objet (runner de test aussi) ;
    `stream-json` émet du **NDJSON** dont l'événement `{"type":"result",…}` porte le verdict final. On le
    retrouve **par ligne** (rapide) plutôt que par un scan char-à-char O(n²) sur un gros transcript. Ordre :
    objet unique → dernier `type==result` → dernier objet valide → fallback préambule. PUR."""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj                       # objet unique (--output-format json / runner injecté)
    except (ValueError, TypeError):
        pass
    result_ev: dict | None = None
    last: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(o, dict):
            last = o
            if o.get("type") == "result":
                result_ev = o
    return result_ev or last or _trailing_json_object(text)


def _rate_limit_rejection(text: str) -> dict | None:
    """Le `rate_limit_info` du **dernier** event `{"type":"rate_limit_event", …}` du stream dont le statut est
    `"rejected"` — le plafond (`five_hour` org, overage désactivé) qui fait rejeter `claude -p` AVANT tout
    travail. C'est une ligne NDJSON **distincte** de l'event `result` (que `_result_event` lit), d'où sa
    propre passe. STRICT : seul `status=='rejected'` compte (un event rate-limit informatif, non rejeté, ne
    classe rien) → un vrai échec instantané n'est jamais confondu avec un rate-limit. PUR."""
    hit: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or '"rate_limit_event"' not in line:
            continue
        try:
            o = json.loads(line)
        except (ValueError, TypeError):
            continue
        info = o.get("rate_limit_info") if isinstance(o, dict) else None
        if o.get("type") == "rate_limit_event" and isinstance(info, dict) \
                and info.get("status") == "rejected":
            hit = info
    return hit


# Clés d'usage token à persister (v13). Toujours présentes dans le dict retourné (None si absent) → un run
# raté ne fabrique jamais un faux zéro ; `record_finish` les lit par `.get()`.
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "model")


def _usage_fields(obj: dict) -> dict:
    """Extrait de l'event `result` l'usage token CUMULÉ (`usage`) + le modèle DOMINANT (`modelUsage`, clé au
    `costUSD` max ; à défaut de coût, la 1ʳᵉ clé). PUR. Chaque champ absent/mal typé → None : on ne synthétise
    pas de zéro (l'absence d'usage est honnête, distincte d'un run à 0 token). Le $ n'est PAS relu ici — il
    reste `total_cost_usd` (cf. `cost_usd`), pas de table de prix locale."""
    usage = obj.get("usage")
    u: dict = usage if isinstance(usage, dict) else {}

    def _int(v: object) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    model: str | None = None
    mu = obj.get("modelUsage")
    if isinstance(mu, dict) and mu:
        priced = [(k, v["costUSD"]) for k, v in mu.items()
                  if isinstance(v, dict) and isinstance(v.get("costUSD"), (int, float))
                  and not isinstance(v.get("costUSD"), bool)]
        model = max(priced, key=lambda kv: kv[1])[0] if priced else next(iter(mu))
    return {"input_tokens": _int(u.get("input_tokens")),
            "output_tokens": _int(u.get("output_tokens")),
            "cache_read_tokens": _int(u.get("cache_read_input_tokens")),
            "cache_creation_tokens": _int(u.get("cache_creation_input_tokens")),
            "model": model}


def parse_headless_result(stdout: str, returncode: int = 0) -> dict:
    """Normalise la sortie de `claude -p` (`--output-format json` OU `stream-json` NDJSON → event `result`).
    PUR. Fail-LOUD : rc≠0, sortie vide, JSON illisible, `is_error`/`api_error_status` → `ok=False` + `error`
    (jamais de faux-vert). Un **rejet rate-limit** (`rate_limit_event status:"rejected"`) est classé À PART :
    `rate_limited=True` + `rate_limit_info` (plafond org, pas un échec) — cf. `_rate_limit_rejection`.
    Tolérant au préambule. Retour : {ok, is_error, result, session_id, cost_usd,
    num_turns, duration_ms, error, rate_limited, rate_limit_info, raw} + l'usage token
    (`input_tokens`/`output_tokens`/`cache_read_tokens`/
    `cache_creation_tokens`/`model`, cf. `_usage_fields`). `duration_ms` (wall du run rapporté par Claude,
    None si absent) sert notamment à donner un `wall_s` non-null quand un job est finalisé depuis son
    transcript (réconciliation d'orphelin)."""
    none_usage = dict.fromkeys(_USAGE_KEYS)
    base = {"ok": False, "is_error": True, "result": None, "session_id": None,
            "cost_usd": None, "num_turns": None, "duration_ms": None, "error": None, "raw": stdout,
            "rate_limited": False, "rate_limit_info": None, **none_usage}
    text = (stdout or "").strip()
    # Rejet rate-limit : classé AVANT le verdict générique (rc≠0 / is_error) — le plafond 5 h de l'org
    # rejette `claude -p` sans que ce soit un échec de la task (détecté sur l'event explicite). La feature
    # sera TENUE (pas `failed`), re-dispatchable après reset (cf. orchestrator).
    rl = _rate_limit_rejection(text)
    if rl is not None:
        return {**base, "rate_limited": True, "rate_limit_info": rl,
                "error": f"rate-limit {rl.get('rateLimitType') or '?'} rejeté "
                         f"(overage {rl.get('overageStatus') or '?'})"}
    if returncode != 0:
        snippet = (stdout or "").strip()[:200] or "(vide)"
        return {**base, "error": f"claude -p rc={returncode} : {snippet}"}
    if not text:
        return {**base, "error": "sortie vide de claude -p"}
    obj = _result_event(text)                # objet unique OU événement `result` du NDJSON stream-json
    if obj is None:
        return {**base, "error": f"sortie non-JSON : {text[:200]}"}
    if not isinstance(obj, dict):
        return {**base, "error": f"JSON inattendu (pas un objet) : {text[:120]}"}
    is_error = bool(obj.get("is_error")) or (obj.get("api_error_status") not in (None, ""))
    err = None
    if is_error:
        err = obj.get("api_error_status") or obj.get("subtype") or "claude a signalé is_error"
    duration = obj.get("duration_ms")
    return {"ok": not is_error, "is_error": is_error, "result": obj.get("result"),
            "session_id": obj.get("session_id"), "cost_usd": obj.get("total_cost_usd"),
            "num_turns": obj.get("num_turns"),
            "duration_ms": duration if isinstance(duration, (int, float))
            and not isinstance(duration, bool) else None,
            "error": err, "raw": stdout, "rate_limited": False, "rate_limit_info": None,
            **_usage_fields(obj)}
