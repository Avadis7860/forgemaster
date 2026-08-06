"""Tests de `db.store` — la base monte, mais elle ne descend jamais, et elle ne s'ouvre pas à l'aveugle.

Ce qui est prouvé ici n'est pas la plomberie de la connexion : c'est la **seconde moitié du garde de
l'invariant**. `restore.check_compatibility` ferme le chemin de la *restauration* ; ce module ferme celui de
l'*ouverture normale*, que le daemon prend à chaque démarrage et sur lequel atterrit quiconque rebascule son
lien `current` à la main.

Le dernier test du fichier est le plus important, et il ne parle pas de `store` : il prouve que le refus
**n'enferme pas la porte de secours**. C'est cette constatation — mesurée, pas supposée — qui a permis de
choisir un refus SEC plutôt qu'une lecture seule. Elle doit rester exécutable : le jour où un verbe de
secours se mettra à ouvrir la base, c'est ici que ça rougira, et pas six mois plus tard chez un utilisateur.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from forgemaster.config import Settings
from forgemaster.db import schema, store


def _settings(tmp: Path) -> Settings:
    return Settings.resolve(home=tmp / "home", projects_root=tmp / "projects")


def _poser_schema(settings: Settings, version: int) -> None:
    """Écrit `user_version` en dur sur la base de l'instance — c'est exactement ce que produirait un binaire
    plus récent ayant migré, puis un retour arrière du seul lien `current`."""
    conn = store.connect(settings.db_path)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()


def test_une_base_vierge_monte_et_une_base_en_retard_aussi(tmp_path: Path) -> None:
    """Non-régression : le sens qui marchait doit continuer à marcher. Une base vierge est créée au schéma
    courant ; une base laissée en arrière est rattrapée sans un mot."""
    settings = _settings(tmp_path)
    conn = store.open_db(settings)
    assert schema.schema_version(conn) == schema.SCHEMA_VERSION
    conn.close()

    _poser_schema(settings, 1)
    conn = store.open_db(settings)
    assert schema.schema_version(conn) == schema.SCHEMA_VERSION
    conn.close()


def test_une_base_trop_neuve_refuse_de_s_ouvrir(tmp_path: Path) -> None:
    """LE test. `migrate()` ne réagissait qu'au retard (`< SCHEMA_VERSION`) : une base d'un schéma SUPÉRIEUR
    passait en silence, et le produit travaillait sur une forme qu'il ne connaît pas. Aucune down-migration
    n'existe pour rattraper, donc le seul instant où l'on peut encore refuser est celui-ci."""
    settings = _settings(tmp_path)
    store.open_db(settings).close()
    _poser_schema(settings, schema.SCHEMA_VERSION + 1)

    with pytest.raises(store.SchemaTooNew) as exc:
        store.open_db(settings)

    message = str(exc.value)
    assert str(schema.SCHEMA_VERSION + 1) in message and str(schema.SCHEMA_VERSION) in message
    # Le message dit le GESTE qui débloque, sur le patron de `update.preflight` et de
    # `restore.check_compatibility` — jamais un « impossible » nu.
    assert "snapshot restore" in message
    assert store.FORCE_FLAG in message


def test_le_refus_n_ecrit_rien_et_ne_laisse_pas_la_base_ouverte(tmp_path: Path) -> None:
    """Un garde qui abîme ce qu'il protège n'en est pas un : le schéma trouvé doit être intact après le
    refus, et la connexion refermée (sous WAL, un descripteur abandonné laisse un `-shm` derrière lui)."""
    settings = _settings(tmp_path)
    store.open_db(settings).close()
    trop_neuf = schema.SCHEMA_VERSION + 3
    _poser_schema(settings, trop_neuf)

    with pytest.raises(store.SchemaTooNew):
        store.open_db(settings)

    conn = sqlite3.connect(str(settings.db_path))
    try:
        assert schema.schema_version(conn) == trop_neuf
    finally:
        conn.close()


def test_la_porte_nommee_ouvre_sans_migrer(tmp_path: Path) -> None:
    """`--allow-unknown-schema` assume, elle ne répare pas : la base s'ouvre et son schéma reste celui qu'il
    était. Une porte qui migrerait en douce serait pire que le défaut qu'elle contourne."""
    settings = _settings(tmp_path)
    store.open_db(settings).close()
    trop_neuf = schema.SCHEMA_VERSION + 1
    _poser_schema(settings, trop_neuf)

    forcee = Settings.resolve(home=settings.home, projects_root=settings.projects_root)
    from dataclasses import replace
    conn = store.open_db(replace(forcee, allow_unknown_schema=True))
    try:
        assert schema.schema_version(conn) == trop_neuf
    finally:
        conn.close()


def test_la_porte_ne_se_prend_pas_par_l_environnement() -> None:
    """La porte vaut pour UNE invocation. `Settings.resolve` ne doit ni la lire dans l'environnement ni
    l'exposer en paramètre : un garde désactivable une fois pour toutes, dans un `forgemaster.env` que
    personne ne relit, n'est plus un garde."""
    assert Settings.resolve().allow_unknown_schema is False
    with pytest.raises(TypeError):
        Settings.resolve(allow_unknown_schema=True)      # type: ignore[call-arg]


def test_la_porte_de_secours_reste_ouverte_sur_une_base_trop_neuve(tmp_path: Path) -> None:
    """C'EST CE TEST QUI A PERMIS DE CHOISIR LE REFUS SEC (arbitrage du 2026-08-06).

    Un refus qui bloque aussi les verbes servant à sortir de l'ornière serait un check défaillant. Mesuré
    plutôt que supposé : `snapshot list` et `update apply` n'appellent pas `open_db` (le premier n'ouvre
    aucune base, le second passe par `connect`, qui ne migre pas). Ce test fige la mesure — si un jour l'un
    d'eux se met à ouvrir la base, il rougit ici, pas chez l'utilisateur qui n'a plus que ça sous la main.
    """
    from forgemaster import cli

    settings = _settings(tmp_path)
    store.open_db(settings).close()
    _poser_schema(settings, schema.SCHEMA_VERSION + 1)

    # `--home` est porté par la SOUS-commande (parser `common` en parent), pas par la racine — le mettre
    # devant fait sortir argparse en usage. Même piège que celui déjà noté dans `apply_update.take_snapshot`.
    assert cli.main(["snapshot", "list", "--home", str(settings.home)]) == 0
    # `update apply` refuse (pas d'unité systemd dans un tmp_path) — mais il refuse pour SA raison, en
    # arrivant jusqu'à son preflight, pas en butant sur la base.
    assert cli.main(["update", "apply", "--home", str(settings.home), "--wheel", "/inexistant.whl"]) == 1
