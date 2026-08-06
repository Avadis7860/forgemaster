"""store — ouverture et migration de la base SQLite du forgemaster. Connexion configurée une fois
(row_factory dict-like, FK ON, WAL pour la concurrence CLI↔daemon, busy_timeout pour l'orchestrateur
parallèle), migration idempotente.

La migration ne va que dans **un** sens. Une base **en retard** monte ; une base **trop neuve** — dont le
`user_version` dépasse le `SCHEMA_VERSION` du binaire en place — est **refusée**, jamais ouverte en silence.
C'est la seconde moitié du garde de l'invariant : `restore.check_compatibility` ferme le chemin de la
*restauration*, celui-ci ferme le chemin de l'*ouverture normale*, que le daemon prend à chaque démarrage.
Sans lui, le produit travaillerait sur un schéma qu'il ne connaît pas — colonnes inconnues ignorées, colonnes
attendues absentes — et aucune down-migration n'existe pour rattraper.

Le refus est **sec**, et c'est tenable parce que la porte de secours ne passe pas par ici : `snapshot list`,
`snapshot restore` et `doctor` n'ouvrent aucune base, et `update apply` passe par `connect` (sans migrer).
Cette constatation est ce qui justifie la forme du refus — `tests/test_store.py` la rend exécutable, pour
qu'elle ne se périme pas en silence le jour où un verbe de secours se mettra à ouvrir la base.

Le refus a une **jumelle non levante** : `readiness()`. Elle pose la même question — *cette base est-elle
lisible par ce binaire ?* — sans agir et sans lever, pour que le daemon puisse le **dire** au lieu de mourir.
Les deux partagent le même texte (`_unreadable`) : deux formulations du même invariant, ce serait deux façons
de le comprendre, donc une de trop.

Le CRUD haut-niveau (create_project/list_features/…) sera porté à la phase logique via `projects.registry`
et consorts, qui reçoivent une connexion — jamais un module-global (correctif anti god-module)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.db import schema

FORCE_FLAG = "--allow-unknown-schema"


class SchemaTooNew(Exception):
    """La base porte un schéma que ce binaire ne connaît pas. Levée **avant** toute écriture."""


def _unreadable(found: int) -> str:
    """Le message, écrit **une** fois : `migrate` le lève, `readiness` le rend. Il nomme trois gestes qui
    débloquent — un refus qui dit seulement « impossible » laisse l'utilisateur sans issue."""
    return (f"cette base porte le schéma {found}, et ce forgemaster ne sait lire que jusqu'au "
            f"{schema.SCHEMA_VERSION}. L'ouvrir travaillerait sur un schéma inconnu, et la base monte en "
            f"forward-only : aucune down-migration n'existe pour rattraper.\n"
            f"  → `forgemaster snapshot list` puis `forgemaster snapshot restore <instantané>` "
            f"(ces verbes n'ouvrent pas la base et répondent encore)\n"
            f"  → ou rebascule <home>/current vers le venv qui écrivait ce schéma\n"
            f"  → ou, si tu sais ce que tu fais : {FORCE_FLAG}")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Ouvre une connexion configurée : dossier parent créé, `Row` dict-like, FK activées, WAL."""
    fp = Path(db_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(fp))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # busy_timeout : sous `forgemaster run` (orchestrateur parallèle), N connexions-par-thread écrivent la
    # même
    # base ; WAL autorise N lecteurs + 1 écrivain → deux écrivains concurrents donnent SQLITE_BUSY. 5 s de
    # retry absorbent les rares chevauchements (fenêtres d'écriture minuscules, le run est du subprocess sans
    # I/O DB) sans sérialiser le parallélisme. Bénin pour tous les appelants mono.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection, *, allow_unknown: bool = False) -> int:
    """Applique le schéma si la base est vierge (ou en retard), **refuse** si elle est trop neuve.
    Idempotent. Retourne la version finale. `create_schema` gère à la fois la base neuve (tout par DDL) et
    l'évolution en place d'une base d'une version antérieure (tables neuves via `IF NOT EXISTS`, colonnes
    neuves via `ensure_columns`).

    `allow_unknown` est la porte de `--allow-unknown-schema` : elle ne « répare » rien et ne migre rien, elle
    assume.
    Elle existe parce qu'un garde sans recours enfermerait l'utilisateur qui a raison contre lui — mais elle
    ne se prend pas par défaut, sinon ce n'est plus un garde."""
    found = schema.schema_version(conn)
    if found > schema.SCHEMA_VERSION and not allow_unknown:
        raise SchemaTooNew(_unreadable(found))
    if found < schema.SCHEMA_VERSION:
        schema.create_schema(conn)
    return schema.schema_version(conn)


def open_db(settings: Settings) -> sqlite3.Connection:
    """Connexion migrée à la base du forgemaster (`settings.db_path`). Point d'entrée des couches.

    La porte se lit dans `settings`, pas dans un argument : elle vaut pour l'invocation entière et il y a
    une trentaine d'appelants — la faire voyager en paramètre aurait demandé de la reposer à chaque étage,
    donc de l'oublier quelque part. `Settings` est déjà l'objet injecté explicitement partout.

    Referme la connexion avant de propager un refus : une base qu'on refuse d'ouvrir ne doit pas laisser
    derrière elle un descripteur ouvert (ni, sous WAL, un `-shm` que personne ne referme)."""
    conn = connect(settings.db_path)
    try:
        migrate(conn, allow_unknown=settings.allow_unknown_schema)
    except SchemaTooNew:
        conn.close()
        raise
    return conn


def readiness(settings: Settings) -> tuple[bool, str]:
    """*Cette instance peut-elle servir ?* — la question de `migrate`, posée sans agir et sans lever.

    Passe par `connect` et **pas** par `open_db` : une sonde qui migrerait la base ferait de la lecture d'un
    état une modification de cet état. Elle ne répond qu'à **une** question — pas un agrégat de vérifications
    qui rougirait sur ce qui est normal — et rend le message de `_unreadable`, celui-là même que porterait le
    refus, avec ses gestes qui débloquent.

    Deux façons de répondre non : le schéma dépasse ce que ce binaire sait lire, ou le fichier n'est pas une
    base lisible du tout. C'est la même question, pas deux. Si la porte `--allow-unknown-schema` est ouverte,
    l'instance sert pour de bon et la sonde le dit — mentir dans ce sens-là serait un faux-rouge."""
    try:
        conn = connect(settings.db_path)
    except sqlite3.Error as exc:
        return False, f"la base {settings.db_path} ne s'ouvre pas : {exc}"
    try:
        found = schema.schema_version(conn)
    except sqlite3.DatabaseError as exc:
        return False, (f"la base {settings.db_path} n'est pas lisible ({exc}). Un instantané la remplace : "
                       f"`forgemaster snapshot list` puis `forgemaster snapshot restore <instantané>`.")
    finally:
        conn.close()
    if found > schema.SCHEMA_VERSION and not settings.allow_unknown_schema:
        return False, _unreadable(found)
    return True, ""
