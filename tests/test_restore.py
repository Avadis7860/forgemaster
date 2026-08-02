"""Tests de `restore` — remettre un instantané, et prouver que ça remet VRAIMENT.

Le piège de cette capacité n'est pas de planter : c'est de **réussir sans rien restaurer**. Écraser le seul
`cockpit.db` en laissant son `-wal` fait rejouer le journal de l'ancienne base par-dessus le fichier remis —
la donnée qu'on voulait défaire revient, sans une seule erreur. Le premier test ci-dessous est celui qui
tient tout le module ; les autres gardent les propriétés qui rendent le geste praticable : on vérifie avant
d'écrire, on met de côté au lieu de détruire, on refuse honnêtement ce qu'on ne comprend pas.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cockpit import restore, snapshot
from cockpit.config import Settings
from cockpit.db import schema, store
from cockpit.secrets.file_store import EncryptedFileStore

_INSERT = ("INSERT INTO projects (id, slug, name, sot_path, created_at) VALUES (?, ?, ?, ?, ?)")


def _seed_projet(conn: sqlite3.Connection, slug: str) -> None:
    conn.execute(_INSERT, (f"id-{slug}", slug, slug, f"/inexistant/{slug}.git", "2026-08-02T00:00:00Z"))


def _slugs(db: Path) -> list[str]:
    conn = sqlite3.connect(str(db))
    try:
        return sorted(r[0] for r in conn.execute("SELECT slug FROM projects"))
    finally:
        conn.close()


@pytest.fixture
def live(tmp_path: Path) -> Settings:
    """Une instance qui a vécu (miroir de la fixture de `test_snapshot`) : base migrée, réglages, coffre."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    _seed_projet(conn, "atelier-fictif")
    conn.commit()
    conn.close()
    (settings.home / "cockpit.env").write_text("COCKPIT_SECRET_STORE=file\n", encoding="utf-8")
    EncryptedFileStore(settings.secrets_dir).put("jeton-fictif", label="forge")
    return settings


def _ecrit_puis_meurt(db: Path, slug: str) -> None:
    """Le daemon tué en plein vol : la transaction est **validée**, mais personne n'a fermé proprement — donc
    aucun checkpoint, et le `-wal` reste sur le disque. C'est l'état réel d'une instance qu'on restaure
    après une MAJ ratée, pas un cas de laboratoire."""
    code = textwrap.dedent(f"""
        import os, signal, sqlite3
        conn = sqlite3.connect({str(db)!r})
        conn.execute({_INSERT!r}, ("id-{slug}", "{slug}", "{slug}", "/x.git", "2026-08-02T00:00:00Z"))
        conn.commit()
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    subprocess.run([sys.executable, "-c", code], check=False)  # noqa: S603
    wal = db.with_name(db.name + "-wal")
    assert wal.is_file() and wal.stat().st_size, "prémisse ratée : aucun -wal orphelin sur le disque"


# --- l'invariant porteur ----------------------------------------------------------------------------

def test_le_journal_de_lancienne_base_ne_ressuscite_pas_ce_quon_defait(live: Settings):
    """LE test du module. Vérifié le 2026-08-02 : écraser le seul `.db` en laissant son `-wal` fait revenir
    la ligne écrite APRÈS l'instantané — SQLite rejoue le journal sur le fichier remis, silencieusement. La
    restauration doit donc écarter `-wal`/`-shm` avec l'ancienne base, sinon elle ne restaure rien."""
    dest = snapshot.create(live)
    _ecrit_puis_meurt(live.db_path, "apres-linstantane")
    # Prémisse lue SANS recouvrer le journal (`immutable=1`) : la ligne validée vit dans le `-wal` et PAS
    # dans le fichier principal. Une lecture ordinaire ici checkpointerait et désamorcerait le piège même
    # qu'on teste — la mutation « on n'écarte plus le -wal » passerait alors au vert (constaté).
    principal_seul = sqlite3.connect(f"file:{live.db_path}?immutable=1", uri=True)
    assert "apres-linstantane" not in [r[0] for r in principal_seul.execute("SELECT slug FROM projects")]
    principal_seul.close()

    restore.restore(dest)

    assert _slugs(live.db_path) == ["atelier-fictif"]           # ← rouge si le `-wal` reste en place
    assert not live.db_path.with_name("cockpit.db-wal").exists()
    assert not live.db_path.with_name("cockpit.db-shm").exists()


# --- refuser avant d'écrire -------------------------------------------------------------------------

def test_une_empreinte_qui_ne_correspond_pas_arrete_tout_avant_decrire(live: Settings):
    """Un instantané abîmé doit faire échouer la restauration **sans avoir touché** à l'instance : à
    moitié restauré est le seul état dont personne ne sait sortir."""
    dest = snapshot.create(live)
    abime = dest / "cockpit.db"
    abime.write_bytes(abime.read_bytes()[:-16] + b"X" * 16)
    _seed_projet(conn := store.connect(live.db_path), "ecrit-depuis")
    conn.commit()
    conn.close()

    with pytest.raises(restore.RestoreError, match="empreinte différente"):
        restore.restore(dest)

    assert "ecrit-depuis" in _slugs(live.db_path)                    # l'instance est intacte
    assert not list(live.home.glob(f"{restore.ASIDE_PREFIX}*"))      # rien n'a même été mis de côté


def test_un_instantane_sans_manifeste_est_refuse(live: Settings):
    dest = snapshot.create(live)
    (dest / snapshot.MANIFEST).unlink()

    with pytest.raises(restore.RestoreError, match="incomplet"):
        restore.restore(dest)


def test_un_schema_inconnu_est_refuse_au_lieu_detre_devine(live: Settings):
    dest = snapshot.create(live)
    manifeste = dest / snapshot.MANIFEST
    data = json.loads(manifeste.read_text(encoding="utf-8")) | {"schema": snapshot.SCHEMA + 99}
    manifeste.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(restore.RestoreError, match="refuse de deviner"):
        restore.restore(dest)


# --- ne rien détruire -------------------------------------------------------------------------------

def test_letat_remplace_est_mis_de_cote_pas_detruit(live: Settings):
    """Restaurer le mauvais instantané doit rester rattrapable — c'est ce qui rend le geste praticable par
    quelqu'un qui doute."""
    dest = snapshot.create(live)
    _seed_projet(conn := store.connect(live.db_path), "travail-du-jour")
    conn.commit()
    conn.close()

    restore.restore(dest)

    (aside,) = live.home.glob(f"{restore.ASIDE_PREFIX}*")
    assert "travail-du-jour" in _slugs(aside / "cockpit.db")     # l'état d'avant est récupérable
    assert (aside / "cockpit.env").is_file()
    assert (aside / "secrets" / "store.enc").is_file()


def test_une_entree_absente_a_la_prise_est_retiree_de_linstance(tmp_path: Path):
    """L'instantané d'une instance qui n'avait pas encore de coffre doit rendre une instance **sans** coffre.
    Laisser le fichier créé depuis, c'est restaurer à moitié en silence."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "p")
    store.open_db(settings).close()
    dest = snapshot.create(settings)                             # ni cockpit.env ni store.enc à ce moment
    EncryptedFileStore(settings.secrets_dir).put("apparu-apres", label="forge")

    restore.restore(dest)

    assert not (settings.secrets_dir / "store.enc").exists()
    (aside,) = settings.home.glob(f"{restore.ASIDE_PREFIX}*")
    assert (aside / "secrets" / "store.enc").is_file()            # retiré, pas détruit


def test_les_modes_declares_sont_reposes(live: Settings):
    dest = snapshot.create(live)
    (live.home / "cockpit.env").chmod(0o666)                     # dégât : le réglage devient world-readable

    restore.restore(dest)

    assert oct((live.home / "cockpit.env").stat().st_mode & 0o777) == "0o600"
    assert oct((live.secrets_dir / "store.enc").stat().st_mode & 0o777) == "0o600"


def test_dry_run_dit_tout_et_necrit_rien(live: Settings, capsys):
    dest = snapshot.create(live)
    _ecrit_puis_meurt(live.db_path, "encore-la-apres")

    restore.restore(dest, dry_run=True)

    sortie = capsys.readouterr().out
    assert "cockpit.db" in sortie and "cockpit.db-wal" in sortie
    assert "encore-la-apres" in _slugs(live.db_path)              # rien n'a bougé
    assert not list(live.home.glob(f"{restore.ASIDE_PREFIX}*"))


# --- les trois portes -------------------------------------------------------------------------------

def test_le_script_voyage_dans_linstantane_et_a_un_chemin_stable(live: Settings):
    """Un chemin stable est ce qu'on peut écrire dans un message d'erreur ; un chemin daté, non."""
    dest = snapshot.create(live)

    fige, stable = dest / snapshot.RESTORE, live.home / snapshot.RESTORE
    assert fige.is_file() and stable.is_file()
    source = Path(restore.__file__).read_bytes()
    assert fige.read_bytes() == source == stable.read_bytes()
    assert os.access(stable, os.X_OK)
    assert snapshot.RESTORE in json.loads(
        (dest / snapshot.MANIFEST).read_text(encoding="utf-8"))["excluded"]   # outillage, pas de l'état


def test_le_script_ne_depend_de_rien_du_cockpit():
    """La contrainte qui rend les deux copies utiles : il tourne avec le `python3` du système sur une
    instance dont le venv est cassé — soit exactement la situation où on restaure."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(Path(restore.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "import relatif : le script ne tournerait plus hors du paquet"
            modules.add((node.module or "").split(".")[0])

    assert "cockpit" not in modules
    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)


def test_la_copie_figee_se_restaure_seule_sans_argument(live: Settings):
    """La ceinture : `python3 restore.py` depuis l'instantané, sans rien savoir d'autre. Lancé sans le
    `src/` du dépôt sur le chemin — s'il importait `cockpit`, il échouerait ici."""
    dest = snapshot.create(live)
    _ecrit_puis_meurt(live.db_path, "apres-linstantane")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

    proc = subprocess.run([sys.executable, snapshot.RESTORE], cwd=dest, env=env,  # noqa: S603
                          capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert _slugs(live.db_path) == ["atelier-fictif"]


def test_sans_instantane_designe_le_script_liste_et_sarrete(live: Settings):
    """Choisir « le plus récent » pour rendre service écraserait un état vivant sur une supposition."""
    dest = snapshot.create(live)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"} | {"COCKPIT_HOME": str(live.home)}

    proc = subprocess.run([sys.executable, str(live.home / snapshot.RESTORE)],  # noqa: S603
                          env=env, capture_output=True, text=True)

    assert proc.returncode == 2
    assert dest.name in proc.stdout and "--snapshot" in proc.stdout
    assert "atelier-fictif" in _slugs(live.db_path)              # aucune restauration devinée


def test_cli_restore_delegue_au_script_fige(live: Settings, capsys):
    """`cockpit snapshot restore` est un lanceur. Le prouver en cassant la copie figée : si la CLI
    réimplémentait la restauration, la casse passerait inaperçue."""
    from cockpit.cli import main

    dest = snapshot.create(live)
    racines = ["--home", str(live.home), "--projects-root", str(live.projects_root)]
    _ecrit_puis_meurt(live.db_path, "apres-linstantane")

    assert main(["snapshot", "restore", dest.name, *racines]) == 0
    assert _slugs(live.db_path) == ["atelier-fictif"]

    (dest / snapshot.RESTORE).write_text("import sys; sys.exit(42)\n", encoding="utf-8")
    assert main(["snapshot", "restore", dest.name, *racines]) == 42      # c'est bien CE fichier qui tourne


def test_cli_restore_refuse_un_instantane_inconnu(live: Settings, capsys):
    from cockpit.cli import main

    assert main(["snapshot", "restore", "2020-01-01T00-00-00Z",
                 "--home", str(live.home), "--projects-root", str(live.projects_root)]) == 1
    assert "introuvable" in capsys.readouterr().err


# --- la preuve fonctionnelle ------------------------------------------------------------------------

def test_apres_restauration_par_le_script_linstance_est_fonctionnellement_identique(live: Settings):
    """La preuve demandée par le plan, par le chemin de l'utilisateur (le script, pas une API interne) :
    instantané → dégâts réels → restauration → mêmes projets, même version de schéma, secrets toujours
    déchiffrables. C'est cette dernière ligne qui prouve le couple choisi au moment de la prise : le blob
    chiffré voyage, la clé maîtresse reste sur l'hôte."""
    ref = EncryptedFileStore(live.secrets_dir).list_entries()[0]["ref"]
    dest = snapshot.create(live)

    EncryptedFileStore(live.secrets_dir).delete(ref)              # dégât 1 : le credential disparaît
    (live.home / "cockpit.env").write_text("COCKPIT_SECRET_STORE=bws\n", encoding="utf-8")  # dégât 2
    _ecrit_puis_meurt(live.db_path, "ecrit-apres-la-maj")         # dégât 3, encore dans le journal

    proc = subprocess.run([sys.executable, str(live.home / snapshot.RESTORE),  # noqa: S603
                           "--snapshot", str(dest)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    conn = store.connect(live.db_path)
    assert schema.schema_version(conn) == schema.SCHEMA_VERSION
    assert sorted(r[0] for r in conn.execute("SELECT slug FROM projects")) == ["atelier-fictif"]
    conn.close()
    assert (live.home / "cockpit.env").read_text(encoding="utf-8") == "COCKPIT_SECRET_STORE=file\n"
    assert EncryptedFileStore(live.secrets_dir).get(ref) == "jeton-fictif"
