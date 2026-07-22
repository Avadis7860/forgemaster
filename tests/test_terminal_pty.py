"""Tests de `terminal.pty` — parties PURES (argv, env couleur, parse_control). Le pont PTY↔WS lui-même
(subprocess + fd) est couvert par l'acceptance/live, pas ici."""
from __future__ import annotations

import os

from cockpit.terminal import pty


def test_local_shell_argv_is_login_bash():
    assert pty.local_shell_argv() == ["/bin/bash", "-l"]


def test_interview_argv_execs_cockpit_interview_in_login_shell():
    """L'interview est une session PTY DÉDIÉE dont le process EST `cockpit interview` : un login shell qui
    `exec`-remplace vers la commande (PATH via `-l`, aucun prompt où une frappe se ré-router, EOF propre à la
    sortie). Le projet est shell-quoté (défense en profondeur, même si les slugs sont kebab-case)."""
    assert pty.interview_argv("void-runner") == ["/bin/bash", "-lc", "exec cockpit interview void-runner"]
    # slug hypothétique à métacaractère → quoté, jamais d'injection shell
    assert pty.interview_argv("a b;rm").count("exec cockpit interview 'a b;rm'") == 1


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
