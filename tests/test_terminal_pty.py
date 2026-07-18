"""Tests de `terminal.pty` — parties PURES (argv, env couleur, parse_control). Le pont PTY↔WS lui-même
(subprocess + fd) est couvert par l'acceptance/live, pas ici."""
from __future__ import annotations

import sys
from pathlib import Path

from cockpit.config import Settings
from cockpit.terminal import pty
from cockpit.tools import tools_bin


def test_local_shell_argv_is_login_bash():
    assert pty.local_shell_argv() == ["/bin/bash", "-l"]


def test_shell_env_is_full_cockpit_shell_with_color(tmp_path):
    """Le PTY web est un VRAI shell cockpit : `cockpit` (bin du venv courant) ET l'outillage (`tools/bin`)
    sur le PATH — sinon `cockpit interview`/`codemap`/`node` seraient `command not found` (le login shell n'a
    qu'un PATH systemd minimal + `~/.local/bin`, bug live 2026-07-18). + TERM couleur injecté (systemd n'en a
    pas → sinon bash/ls/git monochromes ; xterm.js est un xterm-256color)."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    env = pty.shell_env(settings)
    assert env["TERM"] == "xterm-256color"
    assert env["COLORTERM"] == "truecolor"
    path = env["PATH"]
    assert str(Path(sys.executable).parent) in path          # `cockpit` résout dans le terminal
    assert str(tools_bin(settings)) in path                  # `codemap`/`node`/`ruff`… résolvent aussi


def test_parse_control_resize_only():
    assert pty.parse_control('{"type":"resize","cols":80,"rows":24}') == (24, 80)
    assert pty.parse_control('{"type":"other"}') is None
    assert pty.parse_control("pas du json") is None
