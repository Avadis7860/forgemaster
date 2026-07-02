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

from cockpit.core import ids

DISPATCH_ENGINE = "worker-dispatch-v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


# -- état des jobs ----------------------------------------------------------------------------------

def record_start(conn: sqlite3.Connection, *, task_id: str, worktree: str, session_id: str,
                 port: int | None = None, pid: int | None = None, log_path: str | None = None,
                 engine: str = DISPATCH_ENGINE) -> str:
    """Insère un `dispatch_job` en statut `running`, retourne son id. `session_id` = le handle de suivi
    live (le chemin du transcript en dérive)."""
    job_id = ids.new_id()
    conn.execute(
        "INSERT INTO dispatch_jobs (id, task_id, worktree_path, port, pid, status, log_path, "
        "session_id, engine, started_at) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",
        (job_id, task_id, worktree, port, pid, log_path, session_id, engine, _now()))
    conn.commit()
    return job_id


def record_finish(conn: sqlite3.Connection, job_id: str, parsed: dict, *,
                  wall_s: float | None = None, status: str | None = None) -> None:
    """Clôt un job : statut `done` si `parsed.ok`, sinon `failed` (un échec se journalise aussi) ; un
    `status` explicite l'emporte (`killed`/`cancelled`). Renseigne les métriques du run (num_turns, coût)."""
    final = status or ("done" if parsed.get("ok") else "failed")
    conn.execute(
        "UPDATE dispatch_jobs SET status = ?, num_turns = ?, cost_usd = ?, wall_s = ?, "
        "session_id = COALESCE(?, session_id), ended_at = ? WHERE id = ?",
        (final, parsed.get("num_turns"), parsed.get("cost_usd"), wall_s,
         parsed.get("session_id"), _now(), job_id))
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict:
    """Un job par id, ou `KeyError`."""
    r = conn.execute("SELECT * FROM dispatch_jobs WHERE id = ?", (job_id,)).fetchone()
    if r is None:
        raise KeyError(job_id)
    return dict(r)


# -- résolution du transcript + lecture incrémentale (#5) -------------------------------------------

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
