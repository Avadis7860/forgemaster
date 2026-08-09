"""proposals — l'état, PAR VERSION, de ce que l'utilisateur a répondu à une proposition de MAJ (table
`update_proposals`, v21).

La décision du canal s'appelle **propose-consent-preserve** ; `preserve` est construit (instantané, retour
arrière), `propose` ne l'est pas. Ce module en pose la **donnée**, et rien d'autre : aucune surface, aucun
choix pris, aucune application. Le daemon **recueille** un consentement, il n'applique jamais lui-même.

**Pourquoi en base et pas dans un fichier de `home`.** Un choix de l'utilisateur est de la donnée
utilisateur : il doit voyager dans l'instantané pris avant une MAJ, sinon un retour arrière ressusciterait
une proposition refusée — ou effacerait le refus. `snapshot.ENTRIES` prend `forgemaster.db` en
`vacuum-into` : **toute table de cette base est couverte par construction**. C'est la seule raison du choix,
et elle est vérifiée par un test plutôt que déduite (`tests/test_snapshot.py`).

**La clé est la VERSION** — « jamais cette version-là », §1 de la décision. Le `sha` annoncé voyage à côté
et n'est pas la clé : il sert à savoir si la proposition porte encore sur les **mêmes octets**. Une
re-publication sous la même version change le `sha` et **rouvre** la proposition ; sans cette règle un
`declined` couvrirait du code que l'utilisateur n'a jamais vu.

**Best-effort à l'écriture de fond, jamais à la mutation utilisateur** — même partage que `db/alerts.py`
(`emit_alert` best-effort / `ack_alert` lève) : `enregistre` est appelé par le tour du canal, une fois par
tour ; le rater ne doit pas tuer la boucle, et l'annonce repassera. `decide`/`revoque` sont des gestes de
l'utilisateur : elles **lèvent** plutôt que de faire semblant.

**Les quatre verbes arrivent ensemble bien qu'un seul ait un appelant à cette version** (`enregistre`, depuis
le tour du canal). C'est le même motif que l'enum encodé en plein dans le `CHECK` : le cycle de vie d'une
proposition est un tout, et le découper entre deux PR rouvrirait le contrat de la table à la seconde.
`decide` et `revoque` sont exercées par les tests ; leur surface (les trois issues, la révocation depuis les
réglages) est la phase suivante.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

_LOG = logging.getLogger("forgemaster")

_COLS = ("version", "sha", "state", "first_seen_at", "decided_at", "updated_at")

#: Les états qu'un choix de l'utilisateur peut poser. `proposed` n'en fait pas partie : il n'est pas un
#: choix, c'est l'absence de choix — seul le canal l'écrit, et seule `revoque` y ramène.
DECIDABLES = ("deferred", "declined", "accepted")


def _now_iso() -> str:
    """Horodatage ISO-UTC — injectable partout (`now` est un argument, jamais rejoué à l'INSERT)."""
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {c: row[c] for c in _COLS}


def _lire(conn: sqlite3.Connection, version: str) -> sqlite3.Row | None:
    cols = ", ".join(_COLS)
    return conn.execute(
        f"SELECT {cols} FROM update_proposals WHERE version = ?", (version,)).fetchone()


def etat(conn: sqlite3.Connection, *, version: str, sha: str) -> dict | None:
    """La proposition pour `version`, **si elle porte encore sur `sha`**. Rend `None` quand la version est
    inconnue OU que le SHA annoncé a changé — dans les deux cas il n'y a rien qu'on ait le droit d'opposer à
    l'utilisateur. Lecture PURE."""
    row = _lire(conn, version)
    if row is None or row["sha"] != sha:
        return None
    return _row_to_dict(row)


def enregistre(conn: sqlite3.Connection, *, version: str, sha: str, now: str | None = None) -> dict | None:
    """Note qu'une édition **vérifiée** est annoncée. Rend la ligne, ou `None` si l'écriture a échoué.

    Trois cas, tous explicites :

    - version inconnue → une proposition `proposed` naît ;
    - même version, même `sha` → **no-op**, l'état posé par l'utilisateur est préservé tel quel. Le canal
      repasse à chaque tour : rafraîchir `updated_at` à chaque fois n'apprendrait rien et écrirait sur le
      disque toutes les heures pour rien ;
    - même version, `sha` différent → **ré-ouverture** : les octets annoncés ont changé, donc le choix
      précédent ne les couvre plus. `state` revient à `proposed`, `decided_at` est effacé, `first_seen_at`
      est **conservé** (c'est la date à laquelle cette version est apparue, elle ne se réécrit pas).

    **Best-effort** : appelé par la boucle de fond du canal, il ne doit jamais la faire tomber."""
    ts = now or _now_iso()
    try:
        row = _lire(conn, version)
        if row is not None and row["sha"] == sha:
            return _row_to_dict(row)                    # rien de neuf : on ne réécrit pas un choix
        if row is None:
            conn.execute(
                "INSERT INTO update_proposals (version, sha, state, first_seen_at, decided_at, updated_at) "
                "VALUES (?, ?, 'proposed', ?, NULL, ?)", (version, sha, ts, ts))
        else:
            conn.execute(
                "UPDATE update_proposals SET sha = ?, state = 'proposed', decided_at = NULL, "
                "updated_at = ? WHERE version = ?", (sha, ts, version))
        conn.commit()
        # La relecture est DANS le `try` : une promesse de best-effort qui laisse un accès à la base hors
        # de sa garde n'en est pas une — la boucle de fond tomberait sur la lecture après avoir survécu à
        # l'écriture.
        return _row_to_dict(_lire(conn, version))       # type: ignore[arg-type]  # écrite juste au-dessus
    except sqlite3.Error as exc:                        # best-effort : le tour suivant réessaiera
        _LOG.warning("proposition de MAJ non persistée (%s) : %s", version, exc)
        return None


def decide(conn: sqlite3.Connection, *, version: str, sha: str, state: str,
           now: str | None = None) -> dict:
    """Pose le choix de l'utilisateur (`deferred` | `declined` | `accepted`) et rend la ligne.

    **Mutation utilisateur → elle lève, elle ne fait jamais semblant** :
    `ValueError` si `state` n'est pas un choix ; `KeyError` si la version est inconnue **ou si le `sha` a
    changé** — dans ce dernier cas l'utilisateur répond à une proposition qui n'existe plus, et enregistrer
    son consentement contre des octets qu'il n'a pas vus est exactement ce que ce module existe pour
    empêcher."""
    if state not in DECIDABLES:
        raise ValueError(f"état de décision inconnu : {state!r} (attendus : {', '.join(DECIDABLES)})")
    if etat(conn, version=version, sha=sha) is None:
        raise KeyError(version)
    ts = now or _now_iso()
    conn.execute(
        "UPDATE update_proposals SET state = ?, decided_at = ?, updated_at = ? WHERE version = ?",
        (state, ts, ts, version))
    conn.commit()
    return _row_to_dict(_lire(conn, version))           # type: ignore[arg-type]  # existence vérifiée


def revoque(conn: sqlite3.Connection, *, version: str, now: str | None = None) -> dict:
    """Révoque un « ne plus proposer » : `declined` → `proposed`, `decided_at` effacé.

    C'est la moitié que la décision rend obligatoire — un refus « persistant **et** révocable ». Elle ne
    porte que sur `declined` : révoquer un report ou une acceptation ne veut rien dire, et le faire en
    silence serait un cap silencieux. `KeyError` sur une version inconnue, `ValueError` sur un autre état.
    Le `sha` n'est pas demandé : on révoque **son propre refus**, pas une annonce."""
    row = _lire(conn, version)
    if row is None:
        raise KeyError(version)
    if row["state"] != "declined":
        raise ValueError(f"rien à révoquer : {version} est {row['state']!r}, pas 'declined'")
    ts = now or _now_iso()
    conn.execute(
        "UPDATE update_proposals SET state = 'proposed', decided_at = NULL, updated_at = ? "
        "WHERE version = ?", (ts, version))
    conn.commit()
    return _row_to_dict(_lire(conn, version))           # type: ignore[arg-type]  # existence vérifiée
