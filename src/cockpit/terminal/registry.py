"""registry — sessions PTY **détachables** qui survivent à la déconnexion WebSocket.

Un `PtySession` détient le PTY (master fd + login shell) et un **scrollback borné** ; le reader OS est
installé UNE fois au spawn et **survit** détach/reattach — la sortie du shell continue d'être bufferisée
même sans client attaché. Le `PtySessionRegistry` indexe les sessions vivantes **par projet** ; un **reaper**
tue celles détachées trop longtemps (ou déjà mortes).

Discipline calquée sur `dispatch/abort.py` : le `killer` (`killpg`) et l'`clock` sont **injectables**
(invariant forge : I/O injectable → testable sans tuer de vrai process ni attendre l'horloge réelle). Tout
tourne dans l'unique event-loop du daemon (add_reader / reaper / pump) → aucun verrou : la sérialisation est
celle du loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import pty as _pty
import signal
import subprocess
import time
from collections.abc import Callable

# (pgid, signal) -> None. Défaut = groupe de process (shell spawné `start_new_session` → pid == pgid).
Killer = Callable[[int, int], None]
# () -> float monotone. Défaut = time.monotonic ; injecté en test pour piloter le reaper sans dormir.
Clock = Callable[[], float]

READ_SIZE = 65536
SCROLLBACK_CAP = 256 * 1024      # octets de scrollback rejoués à la (re)connexion — fenêtre glissante bornée
DETACH_TTL_S = 30 * 60           # une session détachée > 30 min est reapée (shell + PTY libérés)
REAP_POLL_S = 60.0               # cadence du reaper (idiome sleep-loop, cf. dispatch/stream.py)


def _terminate(proc: subprocess.Popen, killer: Killer, *, grace_s: float = 3.0) -> None:
    """Tue le groupe de process du shell (SIGTERM → grâce → SIGKILL) via le `killer` injecté. Un process déjà
    parti (`ProcessLookupError`) est ignoré. Évite les zombies. Miroir de `dispatch/abort._kill_groups`."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    with contextlib.suppress(ProcessLookupError):
        killer(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_s)
    except Exception:  # noqa: BLE001 — timeout ou proc déjà réappé : on force le SIGKILL
        with contextlib.suppress(ProcessLookupError):
            killer(pgid, signal.SIGKILL)


class PtySession:
    """Un PTY local détachable. Le reader OS (installé au spawn) écrit toujours dans le scrollback borné —
    la session capte la sortie même détachée. Un consommateur (le pump WS) lit le scrollback par **curseur**
    d'offset absolu : mémoire bornée à `cap`, et un client trop lent « saute » à la fenêtre courante au lieu
    de faire gonfler une file (correctif : plus de `Queue` non bornée). L'`epoch` s'incrémente à chaque
    `attach` → l'ancien pump détecte qu'il a été remplacé et sort sans teardown."""

    def __init__(self, project: str, master: int, proc: subprocess.Popen,
                 *, clock: Clock, loop: asyncio.AbstractEventLoop, cap: int = SCROLLBACK_CAP) -> None:
        self.project = project
        self.master = master
        self.proc = proc
        self._clock = clock
        self._loop = loop
        self._cap = cap
        self._scrollback = bytearray()
        self._total = 0                        # octets cumulés émis par le PTY (offset absolu monotone)
        self.eof = False                       # le shell a fermé le PTY (fin de flux)
        self.detached_at: float | None = None  # None = attaché ; sinon horodatage monotone du détach
        self._epoch = 0
        self._reader_installed = False
        self._closed = False
        self.data_event = asyncio.Event()      # réveille le pump (nouvelle sortie / attach / eof / close)

    @classmethod
    def spawn(cls, project: str, argv: list[str], *, cwd: str | None,
              env: dict | None, clock: Clock) -> PtySession:
        """Ouvre un PTY pilotant `argv` (login shell) dans `cwd`, installe le reader OS et retourne la
        session (détachée à 0 client jusqu'au 1ᵉʳ `attach`). Appelé depuis le contexte async → le loop
        courant porte le reader (et son `remove_reader` à la fermeture)."""
        loop = asyncio.get_running_loop()
        master, slave = _pty.openpty()
        proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,  # noqa: S603
                                cwd=cwd, start_new_session=True, close_fds=True, env=env)
        os.close(slave)
        sess = cls(project, master, proc, clock=clock, loop=loop)
        loop.add_reader(master, sess._on_readable)
        sess._reader_installed = True
        return sess

    def _on_readable(self) -> None:
        """Callback du loop quand le master PTY a de la sortie. Écrit dans le scrollback borné (fenêtre
        glissante). Sur EOF (shell terminé) on **retire le reader** immédiatement : un fd en EOF reste
        « lisible » en boucle → sans ça le loop spinnerait à 100 % CPU jusqu'au reap."""
        try:
            data = os.read(self.master, READ_SIZE)
        except OSError:
            data = b""                          # PTY fermé (process terminé) → EOF
        if not data:
            self.eof = True
            self._remove_reader()               # stoppe le spin busy sur fd-en-EOF
        else:
            self._scrollback.extend(data)
            self._total += len(data)
            overflow = len(self._scrollback) - self._cap
            if overflow > 0:
                del self._scrollback[:overflow]  # fenêtre glissante : on ne garde que la queue
        self.data_event.set()

    def attach(self) -> tuple[int, int]:
        """Prend possession pour un nouveau client : incrémente l'epoch (l'ancien pump sortira en
        « remplacé »), (re)marque attaché, réveille le pump. Retourne `(epoch, cursor)` où `cursor` pointe le
        **début du scrollback courant** → le client rejoue tout l'historique disponible."""
        self._epoch += 1
        self.detached_at = None
        self.data_event.set()
        return self._epoch, self._total - len(self._scrollback)

    def detach(self) -> None:
        """Le client est parti mais le shell vit : on horodate le détach (le reaper le TTL-era) et on laisse
        PTY + reader + scrollback intacts. Idempotent."""
        if self.detached_at is None:
            self.detached_at = self._clock()

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def total(self) -> int:
        return self._total

    def alive(self) -> bool:
        """Le shell tourne encore (ni EOF ni process terminé)."""
        return not self.eof and self.proc.poll() is None

    @property
    def exit_code(self) -> int | None:
        """Code de sortie du shell enfant une fois terminé (None s'il tourne encore). `close()`/`_terminate`
        ont fait le `wait()` qui matérialise le code ; on tente un `poll()` de secours si le process est parti
        mais pas encore réappé. Bas-niveau (code brut) — la RAISON (clean/failed_start/crash) est dérivée plus
        haut, dans `pty.classify_exit`."""
        if self.proc.returncode is None:
            with contextlib.suppress(Exception):
                self.proc.poll()
        return self.proc.returncode

    def read_from(self, cursor: int) -> tuple[bytes, int]:
        """Octets du scrollback à partir de l'offset absolu `cursor`, + le nouvel offset. Un curseur tombé
        SOUS la fenêtre glissante (client trop lent) est ramené au début de la fenêtre (perte bornée du
        milieu, jamais de gonflement mémoire) — la queue de sortie reste cohérente."""
        window_start = self._total - len(self._scrollback)
        if cursor < window_start:
            cursor = window_start
        return bytes(self._scrollback[cursor - window_start:]), self._total

    def _remove_reader(self) -> None:
        if self._reader_installed:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self.master)
            self._reader_installed = False

    def close(self, killer: Killer) -> None:
        """Teardown final idempotent : retire le reader, ferme le master, tue le shell. Réveille un pump
        éventuel pour qu'il sorte."""
        if self._closed:
            return
        self._closed = True
        self._remove_reader()
        with contextlib.suppress(OSError):
            os.close(self.master)
        _terminate(self.proc, killer)
        self.data_event.set()


class PtySessionRegistry:
    """Index des sessions PTY vivantes **par projet** (une par projet : le terminal d'Ops est mono-client).
    `clock`/`killer` injectables → propagés aux sessions et au reaper."""

    def __init__(self, *, clock: Clock = time.monotonic, killer: Killer = os.killpg) -> None:
        self._sessions: dict[str, PtySession] = {}
        self.clock = clock
        self.killer = killer

    def get(self, project: str) -> PtySession | None:
        return self._sessions.get(project)

    def put(self, project: str, session: PtySession) -> None:
        self._sessions[project] = session

    def pop(self, project: str, session: PtySession | None = None) -> None:
        """Retire la session du projet — no-op si déjà remplacée par une autre (garde `session` pour ne pas
        évincer une session plus récente sous une pop tardive)."""
        cur = self._sessions.get(project)
        if cur is not None and (session is None or cur is session):
            del self._sessions[project]

    def reap(self, *, ttl_s: float = DETACH_TTL_S) -> list[str]:
        """Ferme et retire les sessions **détachées** qui sont mortes (shell sorti pendant le détach) ou
        détachées depuis > `ttl_s`. Ne touche JAMAIS une session attachée (son pump la possède). Retourne
        les projets reapés."""
        now = self.clock()
        reaped: list[str] = []
        for project, s in list(self._sessions.items()):
            if s.detached_at is None:
                continue                        # attachée → le pump en a la charge
            if not s.alive() or (now - s.detached_at) >= ttl_s:
                s.close(self.killer)
                self.pop(project, s)
                reaped.append(project)
        return reaped

    def close_all(self) -> None:
        """Teardown de toutes les sessions (arrêt du daemon) — attachées comprises."""
        for s in list(self._sessions.values()):
            s.close(self.killer)
        self._sessions.clear()


async def run_reaper(registry: PtySessionRegistry, *, poll_s: float = REAP_POLL_S,
                     ttl_s: float = DETACH_TTL_S) -> None:
    """Tâche de fond : reap périodique des sessions détachées/mortes. Idiome sleep-loop de
    `dispatch/stream.py`. Annulée au shutdown (`_lifespan`)."""
    while True:
        await asyncio.sleep(poll_s)
        reaped = registry.reap(ttl_s=ttl_s)
        if reaped:
            import logging
            logging.getLogger("cockpit").info(
                "reaper terminal : %d session(s) PTY détachée(s) libérée(s) : %s", len(reaped), reaped)
