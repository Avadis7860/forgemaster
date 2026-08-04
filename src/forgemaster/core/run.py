"""run — le seam de transport RÉÉCRIT. Le legacy exécutait tout via `ssh dev@<ip>` après
`resolve_ctid → ProxmoxAPI.lxc_ip` (couplage Proxmox/CT). En édition lightweight WSL, il n'y a ni CT ni
ssh : on exécute **localement** dans le `cwd` du projet, par `subprocess`.

Contrat volontairement mince et PUR (I/O injectable en test des couches hautes) :
- **argv en liste** par défaut (jamais de shell → pas d'injection). Un pipeline shell explicite reste
  possible via `shell=True` pour les rares cas legacy (`… | head -c`), à la charge de l'appelant.
- capture stdout/stderr en texte, retourne un `RunResult` structuré ; `check=True` lève sur rc≠0.
- `timeout` borné : un worker qui pend ne bloque pas l'orchestrateur (→ `RunTimeout`).
"""
from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class RunError(RuntimeError):
    """Commande terminée avec un code de retour non nul alors que `check=True`."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        super().__init__(f"rc={result.returncode} pour {result.argv!r}: {result.stderr.strip()[:200]}")


class RunTimeout(RuntimeError):
    """La commande a dépassé son `timeout` et a été tuée."""


class RunSpin(RunTimeout):
    """`run_streaming` a été coupé par son prédicat `on_line` : le flux n'avançait plus (worker en spin).
    Sous-classe `RunTimeout` — un abort de non-progression est capté partout où un timeout l'est (même
    disposition fail-closed), avec un message distinct porteur de la raison."""


@dataclass(frozen=True)
class RunResult:
    """Résultat structuré d'une exécution locale."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(
    argv: Sequence[str] | str,
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = False,
    input_text: str | None = None,
    shell: bool = False,
) -> RunResult:
    """Exécute `argv` localement dans `cwd`. Remplace le `ssh dev@ip <cmd>` du legacy.

    `argv` est une liste (recommandé, zéro shell) ou une str si `shell=True`. `env`, si fourni, REMPLACE
    l'environnement (l'appelant compose depuis `os.environ` s'il veut hériter — utile pour l'injection
    ciblée `GIT_*` du writeback, cf. spec merge-writeback : env le temps de l'op, jamais persisté)."""
    if shell and not isinstance(argv, str):
        raise TypeError("shell=True exige une commande str")
    if not shell and isinstance(argv, str):
        raise TypeError("argv liste attendu (ou passe shell=True)")

    try:
        proc = subprocess.run(  # noqa: S603 — argv liste sans shell par défaut ; shell explicite opt-in
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunTimeout(f"timeout {timeout}s dépassé pour {argv!r}") from exc

    result = RunResult(
        argv=[argv] if isinstance(argv, str) else list(argv),
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )
    if check and not result.ok:
        raise RunError(result)
    return result


def run_streaming(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    input_text: str | None = None,
    out_path: str | Path | None = None,
    new_session: bool = False,
    on_spawn: Callable[[int], None] | None = None,
    on_line: Callable[[str], str | None] | None = None,
) -> RunResult:
    """Comme `run`, mais **écrit le stdout au fil de l'eau** dans `out_path` (une ligne = un flush) au lieu de
    ne le capturer qu'à la fin — c'est ce qui rend le transcript d'un worker `claude -p --output-format
    stream-json` **suivable en direct** (le pont `dispatch/stream` tail ce fichier). Le stdout complet reste
    accumulé et rendu dans le `RunResult` (le parseur du résultat final le relit). Le `timeout` est honoré
    **même sans aucune sortie** (thread lecteur + `proc.wait(timeout)`, kill → RunTimeout) : un worker qui
    pend ne bloque pas la forge. stdout ET stderr sont drainés en threads → pas de deadlock de pipe. argv en
    LISTE (zéro shell).

    `new_session=True` place le process dans sa **propre session** (`setsid` → il est leader de groupe,
    `pgid == pid`) : un abort peut alors le tuer **en groupe** (`os.killpg`) sans toucher l'orchestrateur —
    c'est le handle explicite qui remplace le `pgrep`/`kill -0` fragile. `on_spawn(pid)`, si fourni, est
    appelé juste après le spawn (le dispatch y persiste le pid en DB → l'abort cross-process le retrouve).

    `on_line(line) -> raison|None`, si fourni, est un **prédicat d'abort injecté** appelé sur chaque ligne
    stdout : la première raison truthy qu'il rend tue le process et fait lever `RunSpin(raison)`. Le cœur
    reste générique — il ne sait pas ce qu'est un « spin » ; la sémantique de non-progression appartient à
    l'appelant (le dispatch). Une exception du prédicat n'interrompt jamais le pompage (avalée)."""
    proc = subprocess.Popen(  # noqa: S603 — argv liste sans shell
        list(argv),
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,                         # ligne-bufferisé → le flush par ligne est effectif
        start_new_session=new_session,     # leader de groupe → tuable en groupe (abort), sinon inchangé
    )
    if on_spawn is not None:
        on_spawn(proc.pid)
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    aborted: dict[str, str] = {}   # {"reason": …} posé par le prédicat on_line → kill + RunSpin après wait

    def _pump_stdout() -> None:
        # Ouvre out_path une fois, écrit+flush chaque ligne dès qu'elle arrive → suivable en live.
        sink = open(out_path, "w", encoding="utf-8") if out_path is not None else None  # noqa: SIM115
        try:
            for line in proc.stdout:       # type: ignore[union-attr]
                out_chunks.append(line)
                if sink is not None:
                    sink.write(line)
                    sink.flush()
                # Prédicat d'abort injecté : la 1re raison truthy tue le process (→ RunSpin après le wait).
                # Défensif : une exception du prédicat ne doit JAMAIS interrompre le drain du pipe.
                if on_line is not None and not aborted:
                    try:
                        reason = on_line(line)
                    except Exception:      # noqa: BLE001 — un prédicat qui plante ne casse pas le pompage
                        reason = None
                    if reason:
                        aborted["reason"] = reason
                        proc.kill()
        finally:
            if sink is not None:
                sink.close()

    def _pump_stderr() -> None:
        for line in proc.stderr:           # type: ignore[union-attr]
            err_chunks.append(line)

    t_out = threading.Thread(target=_pump_stdout, daemon=True)
    t_err = threading.Thread(target=_pump_stderr, daemon=True)
    t_out.start()
    t_err.start()

    if proc.stdin is not None:
        if input_text:
            proc.stdin.write(input_text)
        proc.stdin.close()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        raise RunTimeout(f"timeout {timeout}s dépassé pour {list(argv)!r}") from exc
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=5)

    # Le prédicat on_line a tué le process (kill depuis le thread pump → wait revient sans TimeoutExpired) :
    # on lève APRÈS le join, une fois le stdout drainé, pour une raison porteuse et un flux complet capturé.
    if aborted:
        raise RunSpin(aborted["reason"])

    return RunResult(argv=list(argv), returncode=proc.returncode,
                     stdout="".join(out_chunks), stderr="".join(err_chunks))
