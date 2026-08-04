"""ports — broker de ports **déterministe** pour les worktrees. Chaque worktree de feature reçoit UN port
stable (le `pnpm dev`/serveur du worker y bind) ; le port est relâché au teardown du worktree (merge ET
reset), jamais orphelin (spec worktree-cleanup).

Port de `services/aggregator/ports.py` (`PortStore`), **simplifié pour le mono-hôte WSL** : le legacy
indexait par `(env, port)` car chaque env était un LXC distinct (IP propre) ; ici tout tourne sur un seul
hôte → l'unicité du port est **globale** (`UNIQUE(port)` sur `port_reservations`). On garde l'**algorithme**
gagnant (parcours croissant déterministe + sonde de liberté INJECTÉE + réservation épinglée idempotente) et
on jette la machinerie TTL/heartbeat/reap des serveurs éphémères (inutile : un worktree n'expire pas seul).

Sonde réseau = callable injectée (mockée en test) ; `local_probe` = l'implémentation `connect_ex` locale.
"""
from __future__ import annotations

import socket
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from forgemaster.core import ids

DEFAULT_RANGE: tuple[int, int] = (5170, 5249)   # plage allouable (bornes incluses)
_MAX_RACE_RETRY = 5                              # collisions concurrentes sur free_port → re-tente

# probe(port) -> True si le port semble DÉJÀ pris sur l'hôte (best-effort). None = pas de sonde.
Probe = Callable[[int], bool]


class PortPoolExhausted(RuntimeError):
    """Aucun port libre dans la plage."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def local_probe(port: int) -> bool:
    """True si un service écoute déjà sur `127.0.0.1:<port>` (best-effort, timeout court)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _reserved_ports(conn: sqlite3.Connection) -> set[int]:
    return {r["port"] for r in conn.execute("SELECT port FROM port_reservations")}


def free_port(conn: sqlite3.Connection, *, port_range: tuple[int, int] = DEFAULT_RANGE,
              probe: Probe | None = None) -> int:
    """1er port libre : ni déjà réservé au registre, ni vu pris par la sonde best-effort. DÉTERMINISTE
    (parcours croissant). Lève `PortPoolExhausted` si la plage est saturée."""
    lo, hi = port_range
    taken = _reserved_ports(conn)
    for port in range(lo, hi + 1):
        if port in taken:
            continue
        if probe is not None:
            try:
                if probe(port):
                    continue   # occupé hors-registre → écarté (best-effort)
            except OSError:
                pass           # sonde injoignable → on fait confiance au registre
        return port
    raise PortPoolExhausted(f"plage {lo}-{hi} saturée")


def reserve(conn: sqlite3.Connection, *, project: str, purpose: str,
            port_range: tuple[int, int] = DEFAULT_RANGE, probe: Probe | None = None) -> dict:
    """Réserve un port **stable et idempotent** pour `(project, purpose)` : un re-appel renvoie la même
    réservation (donc le **même port** au re-provision du worktree). Robuste à une course concurrente sur
    `free_port` (deux dispatchs simultanés) via retry sur collision d'unicité."""
    for _ in range(_MAX_RACE_RETRY):
        row = conn.execute(
            "SELECT * FROM port_reservations WHERE project = ? AND purpose = ?",
            (project, purpose)).fetchone()
        if row is not None:
            return dict(row)
        rec = {"id": ids.new_id(), "project": project, "purpose": purpose,
               "port": free_port(conn, port_range=port_range, probe=probe), "created_at": _now()}
        try:
            conn.execute(
                "INSERT INTO port_reservations (id, project, purpose, port, created_at) "
                "VALUES (:id, :project, :purpose, :port, :created_at)", rec)
            conn.commit()
            return rec
        except sqlite3.IntegrityError:
            conn.rollback()   # course : (project,purpose) ou port pris entre-temps → re-boucle
    raise PortPoolExhausted(f"réservation impossible pour {project}:{purpose} (contention)")


def release(conn: sqlite3.Connection, *, project: str, purpose: str) -> dict | None:
    """Relâche la réservation de `(project, purpose)`. Retourne la réservation fermée, ou None si absente
    (idempotent — un double release ne lève pas)."""
    row = conn.execute(
        "SELECT * FROM port_reservations WHERE project = ? AND purpose = ?",
        (project, purpose)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM port_reservations WHERE id = ?", (row["id"],))
    conn.commit()
    return dict(row)


def list_reservations(conn: sqlite3.Connection, *, project: str | None = None) -> list[dict]:
    """Réservations actives (toutes, ou filtrées par projet), triées par port."""
    if project is not None:
        rows = conn.execute(
            "SELECT * FROM port_reservations WHERE project = ? ORDER BY port", (project,))
    else:
        rows = conn.execute("SELECT * FROM port_reservations ORDER BY project, port")
    return [dict(r) for r in rows]
