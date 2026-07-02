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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class RunError(RuntimeError):
    """Commande terminée avec un code de retour non nul alors que `check=True`."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        super().__init__(f"rc={result.returncode} pour {result.argv!r}: {result.stderr.strip()[:200]}")


class RunTimeout(RuntimeError):
    """La commande a dépassé son `timeout` et a été tuée."""


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
