"""pty — pont PTY ↔ WebSocket : ouvre un pseudo-terminal LOCAL dans le workdir d'un projet et relaie
octets et redimensionnements vers/depuis un client web.

Port : `services/aggregator/terminal.py` (`pty_bridge`, parsers). Refactor **#2** : le legacy lançait
`ssh_pty_argv(ip, …)` vers `dev@<CT>` ; ici le PTY est local (`pty`/`os.openpty` sur le shell du workdir),
plus de clé ssh ni d'IP. Refactor **#4** : workdir borné par `core.fs.safe_path(root=projet)`, pas de
`/home/dev` en dur. À considérer : backpressure sur la queue du bridge (faiblesse notée du legacy).

Statut : stub. Signatures figées.
"""
from __future__ import annotations

from pathlib import Path

_SRC = "services/aggregator/terminal.py (pty_bridge)"


def open_pty(workdir: Path, *, shell: str = "/bin/bash") -> int:
    """Ouvre un PTY local sur `shell` dans `workdir`, retourne le fd maître. À porter (refactor #2/#4)."""
    raise NotImplementedError(f"port: {_SRC} — #2 (PTY local, plus de ssh)")
