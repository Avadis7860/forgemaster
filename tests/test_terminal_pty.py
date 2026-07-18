"""Tests de `terminal.pty` — parties PURES (argv, env couleur, parse_control). Le pont PTY↔WS lui-même
(subprocess + fd) est couvert par l'acceptance/live, pas ici."""
from __future__ import annotations

import os

from cockpit.terminal import pty


def test_local_shell_argv_is_login_bash():
    assert pty.local_shell_argv() == ["/bin/bash", "-l"]


def test_shell_env_forces_a_color_terminal():
    """Un service systemd n'a pas de TERM → le PTY doit l'INJECTER, sinon bash/ls/git rendent monochrome.
    xterm.js est un terminal xterm-256color. Le PATH du login shell, lui, N'est PAS posé ici (bash -l le
    ré-dérive via /etc/profile + /etc/profile.d/cockpit-path.sh installé par provision-ct.sh)."""
    env = pty.shell_env()
    assert env["TERM"] == "xterm-256color"
    assert env["COLORTERM"] == "truecolor"
    # hérite bien du reste de l'environnement (PATH etc.), pas un env amputé
    if "PATH" in os.environ:
        assert env.get("PATH") == os.environ["PATH"]


def test_parse_control_resize_only():
    assert pty.parse_control('{"type":"resize","cols":80,"rows":24}') == (24, 80)
    assert pty.parse_control('{"type":"other"}') is None
    assert pty.parse_control("pas du json") is None
