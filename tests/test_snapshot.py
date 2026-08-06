"""Tests de `snapshot` — la prise d'un instantané restaurable AVANT une MAJ.

Ce qui est prouvé ici est ce qui rend la capacité utile, pas sa plomberie : le périmètre est **déclaré**
(la clé maîtresse reste dehors, les logs restent dehors, l'instantané ne s'embarque pas lui-même), la base
copiée est **cohérente et lisible** même prise à chaud, et un instantané interrompu se lit comme
**invalide** au lieu de se restaurer à moitié.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from forgemaster import snapshot
from forgemaster.config import Settings
from forgemaster.db import schema, store
from forgemaster.secrets.file_store import EncryptedFileStore


def _settings(tmp: Path) -> Settings:
    return Settings.resolve(home=tmp / "home", projects_root=tmp / "projects")


def _seed_projet(conn: sqlite3.Connection, slug: str) -> None:
    """Une ligne `projects` minimale — nom fictif (invariant de fixtures du repo)."""
    conn.execute(
        "INSERT INTO projects (id, slug, name, sot_path, created_at) VALUES (?, ?, ?, ?, ?)",
        (f"id-{slug}", slug, slug, f"/inexistant/{slug}.git", "2026-08-02T00:00:00Z"))


@pytest.fixture
def live(tmp_path: Path) -> Settings:
    """Une instance qui a vécu : base migrée avec un projet, réglages, coffre chiffré, logs de jobs."""
    settings = _settings(tmp_path)
    conn = store.open_db(settings)
    _seed_projet(conn, "atelier-fictif")
    conn.commit()
    conn.close()
    (settings.home / "forgemaster.env").write_text("FORGEMASTER_SECRET_STORE=file\n", encoding="utf-8")
    EncryptedFileStore(settings.secrets_dir).put("jeton-fictif", label="forge")
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    (settings.logs_dir / "job-1.log").write_text("x" * 4096, encoding="utf-8")
    return settings


# --- périmètre --------------------------------------------------------------------------------------

def test_le_perimetre_pris_est_celui_declare(live: Settings):
    dest = snapshot.create(live)
    manifest = json.loads((dest / snapshot.MANIFEST).read_text(encoding="utf-8"))

    assert [e["name"] for e in manifest["entries"]] == [
        "forgemaster.db", "forgemaster.env", "secrets/store.enc"]
    assert manifest["absent"] == []
    for entry in manifest["entries"]:
        copied = dest / entry["name"]
        assert copied.exists(), entry["name"]
        assert snapshot._sha256(copied) == entry["sha256"]        # le manifeste décrit CE qui a été écrit
        assert oct(copied.stat().st_mode & 0o777) == f"0o{entry['mode'][1:]}"


def test_la_cle_maitresse_ne_part_jamais_dans_linstantane(live: Settings):
    """Le seul invariant de sécurité de la capacité : l'artefact reste inerte s'il est copié ailleurs."""
    dest = snapshot.create(live)

    assert (live.secrets_dir / "master.key").exists()             # elle est bien là, côté instance
    assert not (dest / "secrets" / "master.key").exists()
    assert not list(dest.rglob("master.key"))
    assert "secrets/master.key" in json.loads(
        (dest / snapshot.MANIFEST).read_text(encoding="utf-8"))["excluded"]


def test_logs_et_snapshots_restent_dehors_et_sont_dits(live: Settings):
    first = snapshot.create(live)                                  # un instantané préexiste déjà…
    dest = snapshot.create(live)                                   # …quand on prend le suivant

    assert not (dest / "logs").exists()
    assert not (dest / "snapshots").exists()                        # sinon croissance quadratique
    assert not list(dest.rglob(first.name))
    excluded = json.loads((dest / snapshot.MANIFEST).read_text(encoding="utf-8"))["excluded"]
    assert {"logs/", "snapshots/", "secrets/master.key"} <= set(excluded)
    assert str(live.projects_root) in excluded


def test_une_entree_absente_nest_ni_prise_ni_exclue(tmp_path: Path):
    """Trois états distincts : prise, absente à la prise, hors périmètre. Une instance neuve n'a ni
    `forgemaster.env` ni coffre — le manifeste doit le DIRE, pas le laisser deviner par un manque."""
    settings = _settings(tmp_path)
    store.open_db(settings).close()

    manifest = json.loads(
        (snapshot.create(settings) / snapshot.MANIFEST).read_text(encoding="utf-8"))

    assert [e["name"] for e in manifest["entries"]] == ["forgemaster.db"]
    assert manifest["absent"] == ["forgemaster.env", "secrets/store.enc"]


# --- cohérence de la base ---------------------------------------------------------------------------

def test_la_base_copiee_est_lisible_et_de_meme_version(live: Settings):
    dest = snapshot.create(live)

    copie = sqlite3.connect(str(dest / "forgemaster.db"))
    assert schema.schema_version(copie) == schema.SCHEMA_VERSION
    assert [r[0] for r in copie.execute("SELECT slug FROM projects")] == ["atelier-fictif"]
    copie.close()


def test_prise_a_chaud_emporte_le_valide_qui_vit_encore_dans_le_wal(live: Settings):
    """LE test qui justifie `VACUUM INTO` plutôt qu'un `cp`. La base est en WAL (`db/store.py`) : tant
    qu'aucun checkpoint n'a eu lieu, une transaction **validée** vit dans le `-wal` et PAS dans le fichier
    principal. Copier le seul fichier principal la perdrait — *« if a database file is separated from its
    WAL file, then transactions that were previously committed to the database might be lost »*. On exige
    donc : le validé-non-checkpointé entre, le non-validé n'entre pas, et la sortie est un fichier
    autonome (aucun `-wal`/`-shm` à transporter avec)."""
    vivant = store.connect(live.db_path)
    _seed_projet(vivant, "validé-non-checkpointé")
    vivant.commit()                                                 # validé → dans le -wal, pas dans le .db
    _seed_projet(vivant, "jamais-validé")                           # transaction ouverte, JAMAIS commit
    # la prémisse du test, pas une supposition
    assert live.db_path.with_name("forgemaster.db-wal").exists()
    principal_seul = sqlite3.connect(f"file:{live.db_path}?immutable=1", uri=True)
    assert "validé-non-checkpointé" not in [
        r[0] for r in principal_seul.execute("SELECT slug FROM projects")]   # un `cp` l'aurait perdu
    principal_seul.close()

    dest = snapshot.create(live)
    vivant.rollback()
    vivant.close()

    assert not list(dest.glob("forgemaster.db-wal")) and not list(dest.glob("forgemaster.db-shm"))
    copie = sqlite3.connect(str(dest / "forgemaster.db"))
    slugs = sorted(r[0] for r in copie.execute("SELECT slug FROM projects"))
    copie.close()
    assert slugs == ["atelier-fictif", "validé-non-checkpointé"]


def test_une_prise_qui_echoue_ne_pose_pas_de_manifeste(live: Settings, monkeypatch):
    """L'invariant « manifeste EN DERNIER », prouvé par l'échec et pas par la lecture : si la prise casse
    en route, le dossier reste **sans** manifeste — donc invalide — au lieu de se laisser restaurer à
    moitié."""
    def _casse(src: Path, dst: Path) -> None:
        raise OSError("disque plein")
    monkeypatch.setattr(snapshot, "_copy_atomic", _casse)

    with pytest.raises(OSError):
        snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["valid"] is False
    assert (Path(lu["path"]) / "forgemaster.db").exists()               # la prise avait bien commencé


def test_le_coffre_reste_dechiffrable_apres_restauration_du_blob(live: Settings, tmp_path: Path):
    """Preuve que le couple choisi (blob dedans / clé dehors) suffit à retrouver ses credentials : on remet
    le `store.enc` de l'instantané par-dessus un coffre qui a divergé, la clé locale le rouvre."""
    ref = EncryptedFileStore(live.secrets_dir).list_entries()[0]["ref"]
    dest = snapshot.create(live)
    EncryptedFileStore(live.secrets_dir).delete(ref)                # dégât : le credential disparaît

    (live.secrets_dir / "store.enc").write_bytes((dest / "secrets" / "store.enc").read_bytes())

    assert EncryptedFileStore(live.secrets_dir).get(ref) == "jeton-fictif"


# --- validité et rétention --------------------------------------------------------------------------

def test_un_dossier_sans_manifeste_se_lit_invalide(live: Settings):
    dest = snapshot.create(live)
    (dest / snapshot.MANIFEST).unlink()                             # prise interrompue avant la fin

    (lu,) = snapshot.list_snapshots(live)
    assert lu["valid"] is False and "incomplet" in lu["reason"]


def test_un_schema_inconnu_est_refuse_honnetement(live: Settings):
    dest = snapshot.create(live)
    manifest = dest / snapshot.MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8")) | {"schema": snapshot.SCHEMA + 99}
    manifest.write_text(json.dumps(data), encoding="utf-8")

    (lu,) = snapshot.list_snapshots(live)
    assert lu["valid"] is False and "inconnu" in lu["reason"]


def test_la_retention_garde_les_plus_recents_et_purge_le_reste(live: Settings):
    dossiers = [snapshot.create(live, keep=3) for _ in range(5)]

    restants = [s["name"] for s in snapshot.list_snapshots(live)]
    assert len(restants) == 3
    assert set(restants) == {d.name for d in dossiers[-3:]}
    assert all(s["valid"] for s in snapshot.list_snapshots(live))


def test_une_prise_interrompue_se_nettoie_a_la_suivante(live: Settings):
    orphelin = snapshot.snapshots_dir(live) / "2026-01-01T00-00-00Z"
    orphelin.mkdir(parents=True)
    (orphelin / "forgemaster.db").write_bytes(b"moitie-ecrit")

    snapshot.create(live)

    assert not orphelin.exists()


def test_purge_refuse_de_tout_supprimer(live: Settings):
    with pytest.raises(ValueError):
        snapshot.create(live, keep=0)


# --- CLI --------------------------------------------------------------------------------------------

def test_cli_create_puis_list(live: Settings, capsys):
    from forgemaster.cli import main

    racines = ["--home", str(live.home), "--projects-root", str(live.projects_root)]

    assert main(["snapshot", "create", *racines]) == 0
    sortie = capsys.readouterr().out
    assert "instantané →" in sortie and "vacuum-into" in sortie
    assert "hors périmètre" in sortie and "master.key" in sortie   # l'exclusion est DITE à la prise

    assert main(["snapshot", "list", *racines]) == 0
    assert "forgemaster.db" in capsys.readouterr().out


# --- les trois états de restauration ----------------------------------------------------------------
#
# Ce que ces tests protègent n'est pas la plomberie de la sonde : c'est que `snapshot list` cesse de
# présenter comme équivalents des instantanés qui ne le sont pas. Le piège nommé ici — « données seules » —
# est celui qui coûte le plus cher : la remise PASSE, puis la base migre en avant, et le retour arrière
# qu'on croyait faire n'a jamais eu lieu. Le rendre visible est tout l'objet de la phase.

def _venv_a(chemin: Path, schema_lu: int) -> Path:
    """Un venv réduit à ce que la sonde interroge : un `bin/python` qui imprime une constante. C'est
    exactement ce que `restore.python_schema` demande — pas un verbe CLI, pas un import réel. Prend un
    chemin ABSOLU, parce que le venv qui compte le plus vit justement hors de `<home>/venvs`."""
    (chemin / "bin").mkdir(parents=True)
    python = chemin / "bin" / "python"
    python.write_text(f"#!/bin/sh\necho {schema_lu}\n", encoding="utf-8")
    python.chmod(0o755)
    return chemin


def _venv_factice(home: Path, nom: str, schema_lu: int) -> Path:
    """Un venv estampillé, sous `<home>/venvs` — celui que le cycle de MAJ crée."""
    return _venv_a(home / snapshot.VENVS / nom, schema_lu)


def _run_de_maj(home: Path, stamp: str, venv_avant: Path | str | None) -> Path:
    """Le journal que `update.launch` laisse derrière lui. Seul `result.json:venv_avant` est lu ici —
    `venv_avant=None` fige le cas d'une instance ANTÉRIEURE à cette clé, qui ne doit rien casser."""
    run = home / "updates" / stamp
    run.mkdir(parents=True)
    verdict: dict[str, object] = {"rc": 0, "verdict": "MAJ posée"}
    if venv_avant is not None:
        verdict["venv_avant"] = str(venv_avant)
    (run / "result.json").write_text(json.dumps(verdict), encoding="utf-8")
    return run


def test_un_instantane_est_restaurable_quand_un_venv_porte_SON_schema(live: Settings):
    """Le seul état qui tient la promesse : binaire ET données reviennent ensemble."""
    _venv_factice(live.home, "2026-08-01T00-00-00Z", schema.SCHEMA_VERSION)
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "restaurable"
    assert str(schema.SCHEMA_VERSION) in lu["state_reason"]


def test_sans_venv_de_son_schema_mais_avec_un_PLUS_HAUT_c_est_donnees_seules(live: Settings):
    """LE piège de cette phase. Aucun venv ne lit le schéma de l'instantané, mais un le dépasse : `restore`
    laissera passer, puis la base migrera EN AVANT — on récupère ses données, on ne revient pas. L'état doit
    NOMMER le schéma vers lequel elle migrerait, sinon le message ne sert à rien au moment où il compte."""
    _venv_factice(live.home, "2026-08-02T00-00-00Z", schema.SCHEMA_VERSION + 1)
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "données seules"
    assert f"le {schema.SCHEMA_VERSION + 1}" in lu["state_reason"]
    assert "forward-only" in lu["state_reason"]


def test_quand_aucun_binaire_ne_lit_assez_loin_l_instantane_est_irrestaurable(live: Settings):
    """Le garde de `restore.check_compatibility` vu depuis la liste : il refusera, et il a raison. Le dire
    AVANT le geste évite de choisir un instantané qui ne se remettra pas."""
    _venv_factice(live.home, "2026-07-01T00-00-00Z", schema.SCHEMA_VERSION - 1)
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "irrestaurable"


def test_sans_venv_a_sonder_l_etat_n_est_pas_mesure_au_lieu_d_etre_rouge(live: Settings):
    """Une instance posée par `pip` n'a pas de `<home>/venvs`. Rendre `irrestaurable` sur ce qui est NORMAL
    ferait un check défaillant — et un check qui s'allume sur du normal finit ignoré, y compris le jour où
    il dit vrai."""
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "inconnu"
    assert "n'a pas été posée par le cycle de MAJ" in lu["state_reason"]


def test_le_venv_le_plus_recent_gagne_quand_deux_portent_le_meme_schema(live: Settings):
    """Deux venvs peuvent lire le même schéma (deux versions produit, un seul schéma). L'état reste
    `restaurable` et nomme le plus récent — celui vers lequel un retour arrière basculerait."""
    _venv_factice(live.home, "2026-08-01T00-00-00Z", schema.SCHEMA_VERSION)
    _venv_factice(live.home, "2026-08-03T00-00-00Z", schema.SCHEMA_VERSION)
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "restaurable"
    assert "2026-08-03T00-00-00Z" in lu["state_reason"]


# --- le PREMIER saut : le venv d'origine vit hors de <home>/venvs -------------------------------------
#
# `<home>/venvs` est CRÉÉ par le premier `update apply` : le venv que `pip install` a posé n'y est jamais.
# Ne sonder que ce dossier rendait le premier saut d'une install fraîche SANS RETOUR, alors que le binaire
# d'avant était intact sur le disque — mesuré le 2026-08-06 sur vrai systemd (VM 9311, portée `user`), et
# invisible pour la preuve de la phase 2a, dont l'acte de retour arrivait après TROIS MAJ. Le premier geste
# d'un utilisateur n'est jamais le troisième geste d'un banc.

def test_le_venv_d_ORIGINE_hors_de_home_venvs_rend_l_instantane_RESTAURABLE(live: Settings, tmp_path: Path):
    """LE cas du premier saut. Un seul venv estampillé, qui lit PLUS LOIN que l'instantané : sans le venv
    d'origine, c'est `données seules` — le piège. Avec lui, c'est `restaurable`, et le motif doit dire OÙ il
    est : `forgemaster` comme nom ne suffit pas à retrouver un binaire."""
    origine = _venv_a(tmp_path / ".venvs" / "forgemaster", schema.SCHEMA_VERSION)
    _venv_factice(live.home, "2026-08-06T00-00-00Z", schema.SCHEMA_VERSION + 1)
    _run_de_maj(live.home, "2026-08-06T00-00-00Z", origine)
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "restaurable"
    assert str(origine) in lu["state_reason"]
    # Et le `restaurable` le dit à voix haute : ce binaire-là, rien ne le protège. Sans cette note, la
    # liste laisserait croire que le produit tient le retour, alors qu'un `rm -rf ~/.venvs` le supprime.
    assert lu["state_note"] is True
    assert "ni compté ni protégé" in lu["state_reason"]


def test_update_venv_pour_designe_le_MEME_venv_que_l_etat(live: Settings, tmp_path: Path):
    """Le couplage entre les DEUX marches, éprouvé à travers `update` — pas seulement dans `snapshot`.

    Elles parcouraient `<home>/venvs` chacune de son côté ; `update._cible_utilisable` porte déjà le refus
    que ça produit (« il se dit `restaurable` mais son venv n'a pas été retrouvé — la liste et la résolution
    ne voient pas le même disque »). Ne réparer QUE `snapshot` aurait transformé un refus explicable en un
    refus incompréhensible. Ce test-ci est celui qui casse dans ce cas-là."""
    from forgemaster import update

    origine = _venv_a(tmp_path / ".venvs" / "forgemaster", schema.SCHEMA_VERSION)
    _venv_factice(live.home, "2026-08-06T00-00-00Z", schema.SCHEMA_VERSION + 1)
    _run_de_maj(live.home, "2026-08-06T00-00-00Z", origine)
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "restaurable"
    assert update._venv_pour(live, Path(lu["path"])) == origine
    assert snapshot.venv_for_schema(live, schema.SCHEMA_VERSION) == origine
    assert str(origine) in lu["state_reason"]


def test_a_schema_egal_l_estampille_passe_devant_le_venv_d_origine(live: Settings, tmp_path: Path):
    """Quand les deux lisent le même schéma, il y a un CHOIX, et il se fait une seule fois. Le cycle de MAJ
    gère les estampillés (il les crée, les compte et les purge) ; le venv d'origine appartient à
    l'utilisateur et n'est que le **repli**. Viser l'origine alors qu'un estampillé équivalent existe
    ferait remonter plus loin que `ROLLBACK_DEPTH` sans le dire."""
    origine = _venv_a(tmp_path / ".venvs" / "forgemaster", schema.SCHEMA_VERSION)
    estampille = _venv_factice(live.home, "2026-08-06T00-00-00Z", schema.SCHEMA_VERSION)
    _run_de_maj(live.home, "2026-08-06T00-00-00Z", origine)

    assert snapshot.venv_for_schema(live, schema.SCHEMA_VERSION) == estampille


def test_un_venv_avant_DISPARU_est_ignore_au_lieu_d_etre_compte(live: Settings, tmp_path: Path):
    """Un `venv_avant` dont le dossier n'existe plus (purgé, ou effacé à la main) ne doit pas devenir une
    cible fantôme : `snapshot list` dirait `restaurable` et le retour arrière échouerait au geste suivant.
    On ne devine pas — on l'ignore, et l'état retombe honnêtement sur ce qui reste."""
    _venv_factice(live.home, "2026-08-06T00-00-00Z", schema.SCHEMA_VERSION + 1)
    _run_de_maj(live.home, "2026-08-06T00-00-00Z", tmp_path / ".venvs" / "jamais-pose")
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "données seules"
    assert snapshot.venv_for_schema(live, schema.SCHEMA_VERSION) is None


def test_un_journal_de_run_INCOMPLET_ne_fait_pas_tomber_la_liste(live: Settings, tmp_path: Path):
    """Trois façons dont un run ne dit rien d'exploitable : tué avant son verdict (pas de `result.json`),
    JSON tronqué, et instance antérieure à la clé `venv_avant`. Aucune n'invalide les AUTRES runs —
    ces dossiers sont des journaux. `snapshot list` est le verbe qu'on lance précisément quand ça va mal."""
    origine = _venv_a(tmp_path / ".venvs" / "forgemaster", schema.SCHEMA_VERSION)
    (live.home / "updates" / "2026-08-04T00-00-00Z").mkdir(parents=True)          # tué avant son verdict
    tronque = live.home / "updates" / "2026-08-05T00-00-00Z"
    tronque.mkdir(parents=True)
    (tronque / "result.json").write_text('{"rc": 0, "verdi', encoding="utf-8")    # JSON tronqué
    _run_de_maj(live.home, "2026-08-06T00-00-00Z", None)                          # antérieur à la clé
    _run_de_maj(live.home, "2026-08-07T00-00-00Z", origine)
    snapshot.create(live)

    (lu,) = snapshot.list_snapshots(live)
    assert lu["state"] == "restaurable"
    assert str(origine) in lu["state_reason"]


def test_les_deux_retentions_derivent_de_LA_politique(live: Settings):
    """`ROLLBACK_DEPTH` est déclarée une fois, chez le module stdlib-pur ; les deux rétentions en dérivent.
    Elles ne coïncident plus par hasard — c'est tout ce que la constante achète, et c'est ce qui casserait
    en silence si quelqu'un re-posait un nombre en dur."""
    from forgemaster.apply_update import KEEP_VENVS, ROLLBACK_DEPTH

    assert KEEP_VENVS == ROLLBACK_DEPTH + 1
    assert snapshot.KEEP == ROLLBACK_DEPTH + 2


def test_cli_list_DIT_le_piege_des_donnees_seules(live: Settings, capsys):
    """L'état ne sert que s'il arrive sous les yeux : le rendu CLI le porte, en toutes lettres."""
    from forgemaster.cli import main

    _venv_factice(live.home, "2026-08-02T00-00-00Z", schema.SCHEMA_VERSION + 1)
    snapshot.create(live)

    assert main(["snapshot", "list", "--home", str(live.home),
                 "--projects-root", str(live.projects_root)]) == 0
    sortie = capsys.readouterr().out
    assert "DONNÉES SEULES" in sortie and "migrera EN AVANT" in sortie


def test_un_manifeste_tronque_ne_fait_pas_PLANTER_la_liste(live: Settings):
    """`snapshot list` est le verbe qu'on lance quand ça va mal — il doit toujours répondre. Un manifeste
    au schéma valide mais sans entrées passait `_read_manifest` (qui tolère l'absence) puis atteignait
    `snapshot_schema` (qui fait `manifest["entries"]`) : `KeyError` au lieu d'une liste. Vérifié reproductible
    avant d'être corrigé."""
    d = snapshot.snapshots_dir(live) / "2026-08-06T00-00-00Z"
    d.mkdir(parents=True)
    (d / snapshot.MANIFEST).write_text(json.dumps({"schema": snapshot.SCHEMA}), encoding="utf-8")

    (lu,) = snapshot.list_snapshots(live)

    assert lu["valid"] is True and lu["state"] == "inconnu"
    assert "tronqué" in lu["state_reason"]
