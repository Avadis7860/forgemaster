"""Tests de `db.proposals` + du pont `update_proposals` — l'état, par version, d'une proposition de MAJ.

Ce qui est prouvé ici est ce qui rend le modèle sûr, pas sa plomberie :

- la table arrive sur une base **qui existait déjà**, et c'est le **bump** qui la fait arriver (le témoin
  rouge : sans bump, rien ne se passe — c'est la mécanique que `docs/schema-contract.md` décrit) ;
- une re-publication sous la même version **rouvre** la proposition : un refus ne couvre jamais des octets
  que l'utilisateur n'a pas vus ;
- une décision utilisateur **lève** au lieu de faire semblant, y compris quand le SHA a bougé sous elle ;
- un seul verdict du canal fait naître une proposition, et les six autres n'en font naître aucune.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from forgemaster import update_channel, update_proposals
from forgemaster.config import Settings
from forgemaster.db import proposals, schema, store

SHA_A = "a" * 40
SHA_B = "b" * 40
T0 = "2026-08-09T10:00:00+00:00"
T1 = "2026-08-09T11:00:00+00:00"


@pytest.fixture
def conn(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    c = store.open_db(settings)
    yield c
    c.close()


# --- la migration ------------------------------------------------------------------------------------

def _base_a_la_version(tmp_path: Path, version: int) -> Settings:
    """Une base qui a vécu SANS la table de la v21, scellée à `version`. On monte le schéma complet puis on
    retire la table et on repose `user_version` : c'est la seule façon de fabriquer l'état d'avant sans
    recopier ici un DDL qui divergerait du vrai à la migration suivante."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    c = store.connect(settings.db_path)
    schema.create_schema(c)
    c.execute("DROP TABLE update_proposals")
    c.execute(f"PRAGMA user_version = {version}")
    c.commit()
    c.close()
    return settings


def _tables(settings: Settings) -> set[str]:
    c = sqlite3.connect(str(settings.db_path))
    noms = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    c.close()
    return noms


def test_la_table_arrive_sur_une_base_QUI_EXISTAIT_DEJA(tmp_path: Path):
    """Une base neuve reçoit tout par `DDL` et ne prouve donc rien du chemin de migration. Ce qu'il faut
    montrer est l'évolution EN PLACE : une base d'hier, ouverte par ce binaire, gagne la table et repart
    scellée à la version courante."""
    settings = _base_a_la_version(tmp_path, schema.SCHEMA_VERSION - 1)
    assert "update_proposals" not in _tables(settings)                  # la prémisse, pas une supposition

    store.open_db(settings).close()

    assert "update_proposals" in _tables(settings)
    c = sqlite3.connect(str(settings.db_path))
    assert schema.schema_version(c) == schema.SCHEMA_VERSION
    c.close()


def test_sans_bump_la_migration_ne_se_declenche_JAMAIS(tmp_path: Path):
    """Le témoin rouge de la règle « tout changement de schéma exige un bump », et sa raison mécanique :
    `store.migrate` n'entre dans `create_schema` que si `found < SCHEMA_VERSION`. Une base qui se déclare
    déjà à la version courante ne re-rentre jamais — la table n'apparaît pas, et tout `SELECT` qui la lirait
    casserait. Sans ce test, la v21 aurait l'air de marcher pour la seule raison qu'on a bumpé par
    habitude."""
    settings = _base_a_la_version(tmp_path, schema.SCHEMA_VERSION)      # scellée à jour, mais sans la table

    store.open_db(settings).close()

    assert "update_proposals" not in _tables(settings)


# --- le cycle de vie d'une proposition ----------------------------------------------------------------

def test_une_annonce_verifiee_fait_naitre_une_proposition_sans_choix(conn):
    ligne = proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0)

    assert ligne == {"version": "0.4.0", "sha": SHA_A, "state": "proposed",
                     "first_seen_at": T0, "decided_at": None, "updated_at": T0}


def test_le_meme_tour_rejoue_ne_reecrit_RIEN(conn):
    """Le canal repasse à chaque tour. Rafraîchir `updated_at` à chaque passage n'apprendrait rien et
    écrirait sur le disque pour rien — mais surtout, un `UPDATE` aveugle finirait un jour par écraser le
    choix de l'utilisateur. On exige donc le no-op strict."""
    proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0)
    proposals.decide(conn, version="0.4.0", sha=SHA_A, state="declined", now=T0)

    apres = proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T1)

    assert apres["state"] == "declined" and apres["updated_at"] == T0


def test_une_republication_sous_la_meme_version_ROUVRE_la_proposition(conn):
    """Le refus portait sur des octets ; ces octets ont changé. Le laisser couvrir la nouvelle publication
    ferait taire une proposition que l'utilisateur n'a jamais vue. `first_seen_at` ne bouge pas : c'est la
    date d'apparition de la VERSION, pas celle de la proposition en cours."""
    proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0)
    proposals.decide(conn, version="0.4.0", sha=SHA_A, state="declined", now=T0)

    rouvert = proposals.enregistre(conn, version="0.4.0", sha=SHA_B, now=T1)

    assert rouvert["state"] == "proposed" and rouvert["sha"] == SHA_B
    assert rouvert["decided_at"] is None
    assert rouvert["first_seen_at"] == T0 and rouvert["updated_at"] == T1


def test_letat_ne_sopposse_pas_a_lutilisateur_quand_le_SHA_a_change(conn):
    proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0)

    assert proposals.etat(conn, version="0.4.0", sha=SHA_A) is not None
    assert proposals.etat(conn, version="0.4.0", sha=SHA_B) is None
    assert proposals.etat(conn, version="9.9.9", sha=SHA_A) is None


@pytest.mark.parametrize("choix", proposals.DECIDABLES)
def test_les_trois_issues_se_posent_et_sont_horodatees(conn, choix: str):
    proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0)

    ligne = proposals.decide(conn, version="0.4.0", sha=SHA_A, state=choix, now=T1)

    assert ligne["state"] == choix and ligne["decided_at"] == T1


def test_une_decision_ne_fait_jamais_semblant(conn):
    """Mutation utilisateur : elle lève. Le cas qui compte est le troisième — l'utilisateur répond à une
    proposition dont les octets ont changé pendant qu'il regardait l'écran. Enregistrer son consentement
    contre ce qu'il n'a pas vu est exactement ce que ce module existe pour empêcher."""
    proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0)

    with pytest.raises(ValueError):
        proposals.decide(conn, version="0.4.0", sha=SHA_A, state="proposed", now=T1)
    with pytest.raises(KeyError):
        proposals.decide(conn, version="9.9.9", sha=SHA_A, state="declined", now=T1)
    with pytest.raises(KeyError):
        proposals.decide(conn, version="0.4.0", sha=SHA_B, state="declined", now=T1)


def test_un_ne_plus_proposer_se_revoque(conn):
    """La moitié que la décision rend obligatoire : « persistant ET révocable ». Sans elle, « ne plus
    proposer » serait un aller sans retour, donc un piège plutôt qu'un choix."""
    proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0)
    proposals.decide(conn, version="0.4.0", sha=SHA_A, state="declined", now=T0)

    revoque = proposals.revoque(conn, version="0.4.0", now=T1)

    assert revoque["state"] == "proposed" and revoque["decided_at"] is None
    assert revoque["sha"] == SHA_A                          # on révoque son refus, pas l'annonce


def test_revoquer_ce_qui_nest_pas_un_refus_le_DIT(conn):
    """Pas de cap silencieux : révoquer un report ou une acceptation ne veut rien dire, et ne rien faire
    en rendant la ligne inchangée laisserait croire que ça a marché."""
    proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0)

    with pytest.raises(ValueError):
        proposals.revoque(conn, version="0.4.0", now=T1)    # elle est 'proposed'
    with pytest.raises(KeyError):
        proposals.revoque(conn, version="9.9.9", now=T1)


def test_une_ecriture_impossible_ne_fait_pas_tomber_le_tour(conn):
    """`enregistre` est appelée par la boucle de fond : best-effort, patron d'`emit_alert`. On casse la
    table pour de vrai plutôt que de simuler — l'annonce repassera au tour suivant."""
    conn.execute("DROP TABLE update_proposals")
    conn.commit()

    assert proposals.enregistre(conn, version="0.4.0", sha=SHA_A, now=T0) is None


# --- le pont : du verdict du canal à la proposition ----------------------------------------------------

def _verdict(etat: str, *, version: str | None = "0.4.0", sha: str | None = SHA_A) -> dict:
    return {"state": etat, "announced": {"version": version, "sha": sha}}


def test_seul_available_fait_naitre_une_proposition(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")

    assert update_proposals.note_verdict(settings, _verdict("available"), now=T0) is not None

    c = store.open_db(settings)
    assert proposals.etat(c, version="0.4.0", sha=SHA_A)["state"] == "proposed"
    c.close()


@pytest.mark.parametrize("etat", ["up-to-date", "unverified", "unreachable", "never",
                                  "no-trust-root", "cannot-situate"])
def test_les_six_autres_verdicts_ne_proposent_RIEN(tmp_path: Path, etat: str):
    """Chacun pour un motif qui lui est propre — rien à proposer, octets non authentifiés, aveu plutôt que
    divergence, ou pure ignorance. La règle est écrite en INCLUSION (`PROPOSABLE`) et pas en liste
    d'exclusions : un huitième état, un jour, ne proposera rien par défaut."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")

    assert update_proposals.note_verdict(settings, _verdict(etat), now=T0) is None


def test_une_annonce_proposable_sans_version_ne_pose_pas_de_ligne_a_cle_vide(tmp_path: Path, caplog):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")

    assert update_proposals.note_verdict(settings, _verdict("available", version=None), now=T0) is None
    assert "proposition non notée" in caplog.text


def test_le_tour_compose_le_tirage_ET_la_note(tmp_path: Path, monkeypatch):
    """La forme qu'attend `run_channel_poll(refresher=...)` : le daemon appelle ceci et gagne la mémoire de
    ce qui a été annoncé, sans rien savoir de la base."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    etat_ecrit = {"last_success": {"at": T0}}
    monkeypatch.setattr(update_proposals.update_channel, "refresh", lambda _s: etat_ecrit)
    monkeypatch.setattr(update_proposals.update_channel, "verdict",
                        lambda _etat, build_sha: _verdict("available"))

    rendu = update_proposals.tour(settings, build_sha="c" * 40)

    assert rendu is etat_ecrit
    c = store.open_db(settings)
    assert proposals.etat(c, version="0.4.0", sha=SHA_A) is not None
    c.close()


# --- la couture avec le VRAI verdict --------------------------------------------------------------------

def test_le_pont_lit_le_VRAI_verdict_pas_une_forme_recopiee_dans_les_tests(tmp_path: Path):
    """Les tests ci-dessus fabriquent le verdict à la main : ils prouvent la règle, pas la **couture**. Si
    `update_channel.verdict` renommait `announced` ou déplaçait `version`/`sha`, ils resteraient verts
    pendant que le pont cesserait de noter quoi que ce soit — un test de couplage ne vaut que par le décor
    où les deux DIVERGERAIENT. Ici le verdict est produit par le vrai code, depuis l'état que `refresh`
    écrit sur disque, et l'instance est **dans la lignée** : le seul décor qui rend `available`."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    nous = "c" * 40
    etat_disque = {"last_success": {"at": T0, "announce": {
        "edition": {"version": "0.4.0", "sha": SHA_A, "committed_at": T0,
                    "wheel": {"name": "forgemaster-0.4.0-py3-none-any.whl", "sha256": "d" * 64}},
        "lineage": [SHA_A, nous]}}}

    v = update_channel.verdict(etat_disque, build_sha=nous)
    assert v["state"] == "available", v                       # la prémisse, mesurée et non supposée

    assert update_proposals.note_verdict(settings, v, now=T0) is not None
    c = store.open_db(settings)
    assert proposals.etat(c, version="0.4.0", sha=SHA_A)["state"] == "proposed"
    c.close()
