"""Tests de `terminal.pty` — parties PURES (argv, env couleur, parse_control, classify_exit) + la logique
d'émission de la frame de contrôle `exit` au teardown (WS + session factices, sans vrai PTY). Le pont PTY↔WS
réel (subprocess + fd) reste couvert par l'acceptance/live."""
from __future__ import annotations

import asyncio
import json
import os

from forgemaster.terminal import pty
from forgemaster.terminal.registry import PtySessionRegistry


def test_local_shell_argv_is_login_bash():
    assert pty.local_shell_argv() == ["/bin/bash", "-l"]


def test_interview_argv_execs_forgemaster_interview_in_login_shell():
    """L'interview est une session PTY DÉDIÉE dont le process EST `forgemaster interview` : un login shell qui
    `exec`-remplace vers la commande (PATH via `-l`, aucun prompt où une frappe se ré-router, EOF propre à la
    sortie). Le projet est shell-quoté (défense en profondeur, même si les slugs sont kebab-case)."""
    assert pty.interview_argv("void-runner") == ["/bin/bash", "-lc", "exec forgemaster interview void-runner"]
    # slug hypothétique à métacaractère → quoté, jamais d'injection shell
    assert pty.interview_argv("a b;rm").count("exec forgemaster interview 'a b;rm'") == 1


def test_shell_env_forces_a_color_terminal():
    """Un service systemd n'a pas de TERM → le PTY doit l'INJECTER, sinon bash/ls/git rendent monochrome.
    xterm.js est un terminal xterm-256color. Le PATH du login shell, lui, N'est PAS posé ici (bash -l le
    ré-dérive via /etc/profile + /etc/profile.d/forgemaster-path.sh installé par provision-ct.sh)."""
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


def test_classify_exit_maps_code_to_reason():
    """`bash -lc 'exec forgemaster …'` : exec échoué (`forgemaster` introuvable/non exécutable) → 127/126 =
    démarrage manqué ; 0 = sortie propre ; tout autre code (ou None = tué sans code) = crash en cours."""
    assert pty.classify_exit(0) == "clean"
    assert pty.classify_exit(127) == "failed_start"   # command not found (forgemaster hors PATH de login)
    assert pty.classify_exit(126) == "failed_start"   # trouvé mais non exécutable
    assert pty.classify_exit(1) == "crash"
    assert pty.classify_exit(137) == "crash"          # SIGKILL (128+9)
    assert pty.classify_exit(None) == "crash"         # process tué sans code matérialisé → crash prudent


class _FakeWS:
    """WebSocket minimal : capture les frames de contrôle TEXTE émises + le close."""

    def __init__(self) -> None:
        self.sent_text: list[str] = []
        self.closed = False

    async def send_text(self, s: str) -> None:
        self.sent_text.append(s)

    async def close(self) -> None:
        self.closed = True


class _FakeSession:
    """Session PTY factice (aucun subprocess/fd) : pilote `alive`/`exit_code`, trace `close`/`detach`."""

    def __init__(self, *, exit_code: int | None = 0, alive: bool = True) -> None:
        self._exit_code = exit_code
        self._alive = alive
        self.closed = False
        self.detached = False

    def alive(self) -> bool:
        return self._alive

    def attach(self) -> tuple[int, int]:
        return 1, 0

    def detach(self) -> None:
        self.detached = True

    def close(self, killer) -> None:  # noqa: ANN001 — killer ignoré (pas de vrai process)
        self.closed = True
        self._alive = False

    @property
    def exit_code(self) -> int | None:
        return self._exit_code


def _serve_with(monkeypatch, *, reason: str, exit_code: int | None = 0, alive: bool = True) -> _FakeWS:
    """Joue `serve_project_terminal` avec un WS + une session factices, en forçant le motif de sortie du
    relais. Retourne le WS pour inspecter les frames émises."""
    ws = _FakeWS()
    session = _FakeSession(exit_code=exit_code, alive=alive)
    registry = PtySessionRegistry()
    registry.put("interview:atlas", session)  # pré-seed → réutilisée, aucun spawn de vrai PTY

    async def _fake_relay(*_args, **_kwargs) -> str:
        return reason

    monkeypatch.setattr(pty, "_relay", _fake_relay)

    async def _scenario() -> None:
        await pty.serve_project_terminal(
            ws, registry, session_key="interview:atlas", argv=[], cwd=None, env=None)

    asyncio.run(_scenario())
    return ws


def _exit_frames(ws: _FakeWS) -> list[dict]:
    frames = [json.loads(t) for t in ws.sent_text]
    return [f for f in frames if f.get("t") == "exit"]


def test_exit_frame_emitted_on_eof_with_reason(monkeypatch):
    """Fin naturelle du PTY (EOF) : une frame `{"t":"exit","code","reason"}` est émise AVANT le close, portant
    la raison dérivée du code — c'est le signal serveur qui permet à l'UI de brancher une erreur technique."""
    ws = _serve_with(monkeypatch, reason=pty._EOF, exit_code=127)
    frames = _exit_frames(ws)
    assert frames == [{"t": "exit", "code": 127, "reason": "failed_start"}]
    assert ws.closed is True


def test_exit_frame_reason_clean_on_zero(monkeypatch):
    ws = _serve_with(monkeypatch, reason=pty._EOF, exit_code=0)
    assert _exit_frames(ws) == [{"t": "exit", "code": 0, "reason": "clean"}]


def test_no_exit_frame_on_disconnect(monkeypatch):
    """Déconnexion client (le shell survit) : on DÉTACHE, aucun verdict de sortie (pas de spectateur) —
    la session n'est ni fermée ni annoncée `exit`."""
    ws = _serve_with(monkeypatch, reason=pty._DISCONNECT, alive=True)
    assert _exit_frames(ws) == []
    assert ws.closed is False


def test_no_exit_frame_on_replaced(monkeypatch):
    """Un nouveau client a pris la session : on sort sans y toucher — pas de frame exit, pas de close."""
    ws = _serve_with(monkeypatch, reason=pty._REPLACED)
    assert _exit_frames(ws) == []
    assert ws.closed is False
