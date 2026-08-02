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

from cockpit import snapshot
from cockpit.config import Settings
from cockpit.db import schema, store
from cockpit.secrets.file_store import EncryptedFileStore


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
    (settings.home / "cockpit.env").write_text("COCKPIT_SECRET_STORE=file\n", encoding="utf-8")
    EncryptedFileStore(settings.secrets_dir).put("jeton-fictif", label="forge")
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    (settings.logs_dir / "job-1.log").write_text("x" * 4096, encoding="utf-8")
    return settings


# --- périmètre --------------------------------------------------------------------------------------

def test_le_perimetre_pris_est_celui_declare(live: Settings):
    dest = snapshot.create(live)
    manifest = json.loads((dest / snapshot.MANIFEST).read_text(encoding="utf-8"))

    assert [e["name"] for e in manifest["entries"]] == ["cockpit.db", "cockpit.env", "secrets/store.enc"]
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
    `cockpit.env` ni coffre — le manifeste doit le DIRE, pas le laisser deviner par un manque."""
    settings = _settings(tmp_path)
    store.open_db(settings).close()

    manifest = json.loads(
        (snapshot.create(settings) / snapshot.MANIFEST).read_text(encoding="utf-8"))

    assert [e["name"] for e in manifest["entries"]] == ["cockpit.db"]
    assert manifest["absent"] == ["cockpit.env", "secrets/store.enc"]


# --- cohérence de la base ---------------------------------------------------------------------------

def test_la_base_copiee_est_lisible_et_de_meme_version(live: Settings):
    dest = snapshot.create(live)

    copie = sqlite3.connect(str(dest / "cockpit.db"))
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
    assert live.db_path.with_name("cockpit.db-wal").exists()        # la prémisse du test, pas une supposition
    principal_seul = sqlite3.connect(f"file:{live.db_path}?immutable=1", uri=True)
    assert "validé-non-checkpointé" not in [
        r[0] for r in principal_seul.execute("SELECT slug FROM projects")]   # un `cp` l'aurait perdu
    principal_seul.close()

    dest = snapshot.create(live)
    vivant.rollback()
    vivant.close()

    assert not list(dest.glob("cockpit.db-wal")) and not list(dest.glob("cockpit.db-shm"))
    copie = sqlite3.connect(str(dest / "cockpit.db"))
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
    assert (Path(lu["path"]) / "cockpit.db").exists()               # la prise avait bien commencé


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
    (orphelin / "cockpit.db").write_bytes(b"moitie-ecrit")

    snapshot.create(live)

    assert not orphelin.exists()


def test_purge_refuse_de_tout_supprimer(live: Settings):
    with pytest.raises(ValueError):
        snapshot.create(live, keep=0)


# --- CLI --------------------------------------------------------------------------------------------

def test_cli_create_puis_list(live: Settings, capsys):
    from cockpit.cli import main

    racines = ["--home", str(live.home), "--projects-root", str(live.projects_root)]

    assert main(["snapshot", "create", *racines]) == 0
    sortie = capsys.readouterr().out
    assert "instantané →" in sortie and "vacuum-into" in sortie
    assert "hors périmètre" in sortie and "master.key" in sortie   # l'exclusion est DITE à la prise

    assert main(["snapshot", "list", *racines]) == 0
    assert "cockpit.db" in capsys.readouterr().out
