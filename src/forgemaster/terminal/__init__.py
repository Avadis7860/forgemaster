"""terminal — pont PTY pour le terminal intégré du web (P5) dont le vocabulaire est la CLI `forgemaster`.
Port de `services/aggregator/terminal.py` (pty_bridge), transport **local** (refactor #2 : plus de
`ssh_pty_argv` vers un hôte distant — on ouvre un PTY sur le shell local dans le workdir du projet)."""
from __future__ import annotations
