"""Tests du retour arrière **VOLONTAIRE** — `forgemaster update rollback`.

Ce que ces tests gardent, dans l'ordre d'importance :

1. **le refus tombe AVANT le premier geste** — cible introuvable, cible qui ne ramènerait pas vraiment,
   travail non commité, ou cible que la prise de sûreté détruirait. Un retour arrière qui refuse à
   mi-parcours est pire que pas de retour arrière : il laisse une instance dans un état que personne n'a
   choisi ;
2. **la cible par défaut ramène binaire ET données** — c'est-à-dire le plus récent instantané que la phase 1
   marque `restaurable`, jamais simplement « le plus récent » ;
3. **le verbe est le symétrique de l'aller, pas un second mécanisme** — même applicateur hors-processus,
   même journal, même vérification en vivant.

Les modes de panne du geste lui-même (moitié restaurée, ordre inversé) sont gardés par la phase 3.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from forgemaster import apply_update, snapshot, update
from forgemaster.config import Settings
from forgemaster.db import schema, store
from forgemaster.update import UpdateRefused

_INSERT = "INSERT INTO projects (id, slug, name, sot_path, created_at) VALUES (?, ?, ?, ?, ?)"


# --- décors -------------------------------------------------------------------------------------------

def _venv(home: Path, nom: str, schema_lu: int) -> Path:
    """Un venv réduit à ce que les deux sondes interrogent : un `bin/python` qui imprime son schéma (c'est
    ce que `restore.python_schema` demande) et un `bin/forgemaster` (présence exigée par le verbe)."""
    venv = home / snapshot.VENVS / nom
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text(f"#!/bin/sh\necho {schema_lu}\n", encoding="utf-8")
    (venv / "bin" / "python").chmod(0o755)
    (venv / "bin" / "forgemaster").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (venv / "bin" / "forgemaster").chmod(0o755)
    return venv


@pytest.fixture
def apres_maj(tmp_path: Path) -> Settings:
    """L'instance TELLE QU'ELLE EST au moment où l'on veut revenir : une MAJ est passée, le lien pointe le
    venv neuf (schéma N+1), l'ancien est encore là (schéma N), et l'instantané pris avant la bascule porte
    le schéma N. C'est exactement l'état que `apply` laisse derrière lui."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    conn.execute(_INSERT, ("id-a", "atelier-fictif", "atelier-fictif", "/x.git", "2026-08-02T00:00:00Z"))
    conn.commit()
    conn.close()
    (settings.home / "forgemaster.env").write_text("FORGEMASTER_SECRET_STORE=file\n", encoding="utf-8")

    snapshot.create(settings)                                   # pris quand la base était au schéma N
    ancien = _venv(settings.home, "2026-08-01T00-00-00Z", schema.SCHEMA_VERSION)
    neuf = _venv(settings.home, "2026-08-05T00-00-00Z", schema.SCHEMA_VERSION + 1)
    os.symlink(neuf, settings.home / "current")
    assert ancien.is_dir()
    return settings


def _unite(settings: Settings, tmp: Path) -> Path:
    """Une unité qui lance le service PAR le lien stable — sans quoi le préflight refuse, à raison."""
    path = tmp / "forgemaster.service"
    path.write_text(
        f"[Service]\nExecStart={settings.home / 'current' / 'bin' / 'forgemaster'} "
        f"serve --host 127.0.0.1 --port 8700\n", encoding="utf-8")
    return path


def _plan(settings: Settings, tmp: Path, **over):
    return update.preflight_rollback(settings, snapshot=over.pop("snapshot", None),
                                     unit=str(_unite(settings, tmp)), scope="user", **over)


# --- 1. la cible par défaut -----------------------------------------------------------------------------

def test_la_cible_par_defaut_est_le_plus_recent_instantane_QUI_RAMENE_VRAIMENT(
        apres_maj: Settings, tmp_path: Path):
    """Pas « le plus récent », mais le plus récent **`restaurable`** — le seul état où binaire et données
    reviennent ensemble. Et le venv résolu est celui dont le forgemaster lit EXACTEMENT ce schéma : un
    binaire qui lit plus loin remettrait les données puis migrerait la base en avant."""
    plan = _plan(apres_maj, tmp_path)

    assert plan["target_venv"].name == "2026-08-01T00-00-00Z"
    assert plan["snapshot"].is_dir() and plan["snapshot_name"] == plan["snapshot"].name
    assert plan["venv"].name == "2026-08-05T00-00-00Z", "le venv ACTUEL doit rester nommé dans le plan"


def test_sans_aucun_instantane_le_verbe_refuse_en_disant_ou_ils_naissent(
        apres_maj: Settings, tmp_path: Path):
    for d in snapshot.snapshots_dir(apres_maj).iterdir():
        for f in sorted(d.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
        d.rmdir()

    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path)

    assert "rien vers quoi revenir" in str(exc.value)
    assert "update apply" in str(exc.value), "le refus doit dire d'où viennent les instantanés"


def test_un_instantane_QUI_NE_RAMENE_PAS_est_refuse_avec_SON_motif(apres_maj: Settings, tmp_path: Path):
    """Le piège nommé par la phase 1, refusé ici plutôt que subi : plus aucun venv ne porte le schéma de
    l'instantané, donc le remettre migrerait la base en avant. Le refus reprend le motif mesuré par
    `snapshot list` — pas une seconde formulation du même invariant."""
    ancien = apres_maj.home / snapshot.VENVS / "2026-08-01T00-00-00Z"
    (ancien / "bin" / "python").write_text(f"#!/bin/sh\necho {schema.SCHEMA_VERSION + 2}\n", encoding="utf-8")

    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path)

    assert "restaurable" in str(exc.value)
    assert "snapshot list" in str(exc.value)


def test_un_instantane_inconnu_est_refuse_en_NOMMANT_ceux_qui_existent(apres_maj: Settings, tmp_path: Path):
    """Un « introuvable » nu oblige à aller chercher ailleurs. Le refus porte la liste."""
    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path, snapshot="2019-01-01T00-00-00Z")

    assert "inconnu" in str(exc.value)
    assert [d.name for d in snapshot.snapshots_dir(apres_maj).iterdir()][0] in str(exc.value)


def test_revenir_vers_le_venv_DEJA_actif_est_refuse(apres_maj: Settings, tmp_path: Path):
    """Il n'y a nulle part où revenir, et le geste serait trompeur : il restaurerait des données sous le
    binaire courant, ce qui n'est pas un retour arrière. Le refus nomme le verbe qui, lui, fait ça."""
    (apres_maj.home / "current").unlink()
    os.symlink(apres_maj.home / snapshot.VENVS / "2026-08-01T00-00-00Z", apres_maj.home / "current")

    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path)

    assert "DÉJÀ actif" in str(exc.value)
    assert "snapshot restore" in str(exc.value)


def _maj_non_migrante(settings: Settings) -> tuple[Path, Path, Path]:
    """L'état que laisse une MAJ **qui ne migre pas la base** — le cas le plus courant, et celui qu'aucun
    banc ne jouait : deux venvs qui lisent le MÊME schéma, un instantané pris avant la bascule, et le
    journal du run qui dit lequel des deux tournait à ce moment-là.

    Rend `(instantané, venv d'avant, venv neuf)`."""
    ancien = settings.home / snapshot.VENVS / "2026-08-01T00-00-00Z"
    neuf = _venv(settings.home, "2026-08-09T00-00-00Z", schema.SCHEMA_VERSION)   # même schéma que l'ancien
    (settings.home / "current").unlink()
    os.symlink(neuf, settings.home / "current")
    (snap,) = sorted(snapshot.snapshots_dir(settings).iterdir())
    run = settings.home / "updates" / "2026-08-09T00-00-00Z"
    run.mkdir(parents=True)
    (run / "result.json").write_text(
        json.dumps({"rc": 0, "verdict": "MAJ posée", "venv_avant": str(ancien), "instantane": str(snap)}),
        encoding="utf-8")
    return snap, ancien, neuf


def test_apres_une_MAJ_NON_MIGRANTE_le_verbe_sait_encore_revenir(apres_maj: Settings, tmp_path: Path):
    """LE défaut de [[rollback-blind-to-non-migrating-update]], vu du verbe. Les deux venvs lisent le même
    schéma que l'instantané ; sans départage, la résolution retenait le plus RÉCENT — c'est-à-dire celui
    qu'on cherche à quitter — et le refus « il correspond au venv DÉJÀ actif » tombait sur les trois
    instantanés d'affilée. Mesuré sur vrai systemd le 2026-08-07, jamais relu."""
    _, ancien, neuf = _maj_non_migrante(apres_maj)

    plan = _plan(apres_maj, tmp_path)

    assert plan["target_venv"] == ancien, "la cible est le binaire qui tournait quand l'instantané a été pris"
    assert plan["venv"].resolve() == neuf.resolve()


def test_les_DEUX_marches_designent_le_meme_venv_apres_une_MAJ_non_migrante(apres_maj: Settings):
    """Le couplage de 2a‴, re-joué sur le cas non migrant : `snapshot list` et la résolution ne peuvent pas
    répondre différemment. Avant le départage, la liste disait `restaurable` ✔ **et** le verbe refusait —
    deux marches, deux réponses, sur la même instance."""
    snap, ancien, _ = _maj_non_migrante(apres_maj)

    (lu,) = [s for s in snapshot.list_snapshots(apres_maj) if s["path"] == str(snap)]
    assert lu["state"] == "restaurable" and str(ancien) in lu["state_reason"]
    assert update._venv_pour(apres_maj, snap) == ancien


def test_le_retour_REJOUE_ne_repart_pas_en_avant_quand_RIEN_n_a_migre(apres_maj: Settings, tmp_path: Path):
    """Le va-et-vient, que le garde de direction ne savait pas voir sans migration. Après un retour, la
    prise de SÛRETÉ est l'instantané le plus récent et son binaire apparié est celui qu'on vient de
    quitter : la viser, c'est **avancer**. Le garde existant compare les schémas — muet quand ils sont
    égaux, c'est-à-dire précisément dans le cas que le départage vient d'ouvrir.

    Ce que le run dit et que le schéma ne dit pas : cet instantané est né d'un `rollback`."""
    _, ancien, neuf = _maj_non_migrante(apres_maj)
    # l'état exact que laisse un retour arrière : lien revenu sur l'ancien, prise de sûreté sous le neuf
    surete = snapshot.create(apres_maj)
    (apres_maj.home / "current").unlink()
    os.symlink(ancien, apres_maj.home / "current")
    run = apres_maj.home / "updates" / "2026-08-10T00-00-00Z"
    run.mkdir(parents=True)
    (run / "result.json").write_text(
        json.dumps({"rc": 0, "mode": "rollback", "venv_avant": str(neuf),
                    "instantane_surete": str(surete)}), encoding="utf-8")

    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path)

    assert "retour arrière" in str(exc.value), "le motif doit nommer ce qu'est cet instantané"
    assert "snapshot list" in str(exc.value)
    # Et la liste ne peut pas le présenter comme la cible du prochain retour : les deux marches répondent
    # à deux questions différentes, ce qui n'autorise pas l'une à laisser croire ce que l'autre refuse.
    (vue,) = [s for s in snapshot.list_snapshots(apres_maj) if s["path"] == str(surete)]
    assert vue["state"] == "restaurable" and vue["safety_of_rollback"] is True


def test_un_instantane_que_PERSONNE_ne_nomme_le_DIT_dans_son_refus(apres_maj: Settings, tmp_path: Path):
    """Le résidu : sans journal, on n'a que l'ordre par récence, et le refus qui en découle aurait l'air
    arbitraire. Il nomme donc ce qui manque, au lieu de laisser croire qu'il n'y a rien sur le disque."""
    _maj_non_migrante(apres_maj)
    for run in (apres_maj.home / "updates").iterdir():
        (run / "result.json").unlink()

    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path)

    assert "DÉJÀ actif" in str(exc.value)
    assert "aucun journal de MAJ ne dit quel binaire tournait" in str(exc.value)


# --- 2. le refus d'autorité, enfin câblé sur le RETOUR --------------------------------------------------

def test_du_travail_non_commite_bloque_AUSSI_le_retour_arriere(apres_maj: Settings, tmp_path: Path):
    """La phase 1a a écrit ce verdict et ne l'a câblé que sur l'aller — `apply_update` est stdlib-pur et ne
    voit ni `projects_root` ni git. Le verbe volontaire est le premier endroit où ce refus peut exister, et
    revenir en arrière pendant qu'un travail non commité vit dans un worktree est exactement le geste à
    refuser : `projects_root` n'entre pas dans l'instantané."""
    verdicts = [{"slug": "atelier-fictif", "state": update.auth.BLOCKING, "detail": "3 fichiers modifiés"}]

    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path, authority=verdicts)

    assert "NON COMMITÉ" in str(exc.value)
    assert "ce retour arrière" in str(exc.value), "le message doit parler du geste RÉEL, pas d'une MAJ"
    assert "atelier-fictif" in str(exc.value)


def test_un_etat_non_bloquant_est_DIT_sans_bloquer(apres_maj: Settings, tmp_path: Path):
    """Arbitrage de la phase 1a, reconduit ici : seul le non-commité bloque. « Aucun remote » est un cas
    normal du produit distribué — le taire serait malhonnête, en faire un refus interdirait le geste."""
    verdicts = [{"slug": "atelier-fictif", "state": "no_remote", "detail": "aucun remote"}]

    lignes = update.describe_rollback(_plan(apres_maj, tmp_path, authority=verdicts))

    assert any("hors instantané" in ligne for ligne in lignes)


# --- 3. la cible protégée de sa PROPRE prise de sûreté --------------------------------------------------

def test_la_prise_de_surete_ne_peut_pas_purger_la_cible_du_retour(tmp_path: Path):
    """Le finding le plus vicieux de cette phase : l'instantané de sûreté consomme un cran de rétention et
    déclenche la purge — il peut donc détruire la cible même du retour arrière. Refusé AVANT le premier
    geste : un refus coûte une relance, une cible détruite ne se rattrape pas."""
    root = tmp_path / "snapshots"
    for nom in ("2026-08-01", "2026-08-02", "2026-08-03"):
        (root / nom).mkdir(parents=True)
        (root / nom / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(apply_update.UpdateFailed) as exc:
        apply_update._refuse_if_target_would_be_purged(root / "2026-08-01", lambda _m: None)

    assert "purgerait" in str(exc.value)
    assert "snapshot list" in str(exc.value), "le refus doit dire comment choisir autrement"


def test_une_cible_assez_recente_passe_la_verification_de_retention(tmp_path: Path):
    """Le symétrique, qui garde la garde : un refus qui tombe sur le cas NOMINAL rendrait le verbe
    inutilisable, et c'est comme ça qu'un garde finit désarmé."""
    root = tmp_path / "snapshots"
    for nom in ("2026-08-01", "2026-08-02", "2026-08-03"):
        (root / nom).mkdir(parents=True)
        (root / nom / "manifest.json").write_text("{}", encoding="utf-8")
    dits: list[str] = []

    apply_update._refuse_if_target_would_be_purged(root / "2026-08-02", dits.append)

    assert any("survivra" in ligne for ligne in dits)


def test_la_retention_des_instantanes_derive_de_LA_politique():
    """`KEEP_SNAPSHOTS` vit chez le module stdlib-pur parce que le verbe doit compter les crans sans rien
    pouvoir importer de `forgemaster` — et `snapshot.KEEP` le lit, jamais l'inverse."""
    assert apply_update.KEEP_SNAPSHOTS == apply_update.ROLLBACK_DEPTH + 2
    assert snapshot.KEEP == apply_update.KEEP_SNAPSHOTS


# --- 4. un MODE, pas un second script -------------------------------------------------------------------

def test_le_mode_rollback_exige_sa_cible_et_le_dit(tmp_path: Path):
    """`required=` d'argparse ne sait pas dépendre d'un autre drapeau. Sans cette vérification, l'absence
    se découvrirait en `AttributeError` trois étapes plus loin — après l'arrêt du service."""
    with pytest.raises(SystemExit):
        apply_update._parse(["--mode", "rollback", "--home", str(tmp_path), "--link", str(tmp_path / "c"),
                             "--run-dir", str(tmp_path / "r"), "--base-url", "http://127.0.0.1:1"])


def test_le_mode_apply_reste_le_defaut_et_exige_son_wheel(tmp_path: Path):
    """Le retour arrière ne doit rien changer à l'aller — mêmes exigences, même défaut."""
    with pytest.raises(SystemExit):
        apply_update._parse(["--home", str(tmp_path), "--link", str(tmp_path / "c"),
                             "--run-dir", str(tmp_path / "r"), "--base-url", "http://127.0.0.1:1"])

    args = apply_update._parse(["--home", str(tmp_path), "--link", str(tmp_path / "c"),
                                "--run-dir", str(tmp_path / "r"), "--base-url", "http://127.0.0.1:1",
                                "--wheel", "x.whl"])
    assert args.mode == "apply"


# --- 5. le verbe, vu du produit -------------------------------------------------------------------------

def test_le_dry_run_DIT_les_deux_gestes_et_leur_ordre(apres_maj: Settings, tmp_path: Path, capsys):
    """La découvrabilité du geste passe par ce qu'il annonce, pas par de la doc (cadrage de la fiche). Il
    doit nommer les DEUX gestes — le lien ET les données — et l'ordre contraint entre eux."""
    from forgemaster.cli import main

    rc = main(["update", "rollback", "--dry-run", "--unit", str(_unite(apres_maj, tmp_path)),
               "--home", str(apres_maj.home), "--projects-root", str(apres_maj.projects_root)])

    sortie = capsys.readouterr().out
    assert rc == 0
    assert "bascule du lien PUIS restauration" in sortie
    assert "instantané de SÛRETÉ" in sortie
    assert "retour du retour" in sortie
    assert "rien n'a été lancé" in sortie


def test_le_refus_du_verbe_sort_en_1_sans_rien_toucher(apres_maj: Settings, tmp_path: Path, capsys):
    """Un refus se lit au code de sortie autant qu'au message — c'est ce qui le rend scriptable."""
    from forgemaster.cli import main

    unite = tmp_path / "endur.service"
    unite.write_text("[Service]\nExecStart=/opt/fige/bin/forgemaster serve --host 127.0.0.1 --port 8700\n",
                     encoding="utf-8")

    rc = main(["update", "rollback", "--unit", str(unite), "--home", str(apres_maj.home),
               "--projects-root", str(apres_maj.projects_root)])

    assert rc == 1
    assert "venv EN DUR" in capsys.readouterr().err
    assert (apres_maj.home / "current").resolve().name == "2026-08-05T00-00-00Z", "rien n'a bougé"


def test_le_lanceur_passe_la_CIBLE_et_pas_un_wheel(apres_maj: Settings, tmp_path: Path, monkeypatch):
    """Un seul lanceur pour les deux gestes : ce que le mode change tient dans les arguments de cible. Si
    `--wheel` fuyait dans un retour arrière, l'applicateur exigerait un fichier qui n'existe pas."""
    vus: dict[str, list[str]] = {}

    def _run(cmd, **_kw):
        vus["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # Le plan se résout AVANT le monkeypatch : la résolution sonde les venvs par `subprocess.run`, que le
    # lanceur emprunte désormais lui aussi. Patcher d'abord ferait échouer la sonde et non le lanceur.
    plan = _plan(apres_maj, tmp_path)
    monkeypatch.setattr(update.subprocess, "run", _run)

    update.launch(apres_maj, plan, systemctl="systemctl", service="forgemaster", detach=True,
                  mode="rollback")

    cmd = vus["cmd"]
    assert "--wheel" not in cmd
    assert cmd[cmd.index("--mode") + 1] == "rollback"
    assert cmd[cmd.index("--target-venv") + 1] == str(plan["target_venv"])
    assert cmd[cmd.index("--snapshot") + 1] == str(plan["snapshot"])


def test_la_correspondance_instantane_binaire_se_DERIVE_du_schema(apres_maj: Settings):
    """Arbitrage du 2026-08-06 : aucun état nouveau, aucune ligne de plus au manifeste. La preuve est que
    le manifeste ne porte AUCUNE trace du venv, et que la résolution marche quand même."""
    dossier = next(iter(snapshot.snapshots_dir(apres_maj).iterdir()))
    manifest = json.loads((dossier / snapshot.MANIFEST).read_text(encoding="utf-8"))

    # Le manifeste nomme `venvs/` comme EXCLUSION (frontière déclarée) ; ce qu'on interdit ici est qu'il
    # nomme un venv PARTICULIER — ce serait l'état stocké que l'arbitrage a écarté.
    assert "2026-08-01T00-00-00Z" not in json.dumps(manifest), "un venv s'est mis à voyager au manifeste"
    assert "2026-08-05T00-00-00Z" not in json.dumps(manifest)
    assert update._venv_pour(apres_maj, dossier).name == "2026-08-01T00-00-00Z"


def test_un_instantane_sans_base_ne_resout_aucun_venv(apres_maj: Settings, tmp_path: Path):
    """Sans base dans l'instantané, il n'y a pas de schéma à faire correspondre — on ne devine pas un venv.
    Le verbe refusera plus haut ; ce qui compte ici est que la résolution rende `None` au lieu d'un choix."""
    dossier = next(iter(snapshot.snapshots_dir(apres_maj).iterdir()))
    manifest = json.loads((dossier / snapshot.MANIFEST).read_text(encoding="utf-8"))
    manifest["entries"] = [e for e in manifest["entries"] if not e["name"].endswith(".db")]
    (dossier / snapshot.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    assert update._venv_pour(apres_maj, dossier) is None


def test_le_schema_de_linstantane_est_bien_celui_de_la_base_prise(apres_maj: Settings):
    """Le fait sur lequel repose toute la résolution, mesuré et pas supposé : `VACUUM INTO` transporte
    `user_version`, donc le schéma se lit dans le `.db` embarqué sans rien ajouter au format."""
    dossier = next(iter(snapshot.snapshots_dir(apres_maj).iterdir()))
    conn = sqlite3.connect(str(dossier / "forgemaster.db"))
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == schema.SCHEMA_VERSION
    finally:
        conn.close()


def test_un_retour_arriere_ne_repart_pas_EN_AVANT(apres_maj: Settings, tmp_path: Path):
    """Trouvé en revue du diff, reproduit sur le produit avant d'être corrigé. Après un retour arrière,
    l'instantané de SÛRETÉ est le plus récent et il est `restaurable` : le prendre par défaut ferait
    repartir vers la version qu'on vient de quitter. « Revenir » a un sens, et ce n'est pas « bouger »."""
    # L'état d'APRÈS un retour arrière : le lien est sur l'ancien, et un instantané de sûreté au schéma du
    # NEUF traîne, plus récent que tous les autres.
    (apres_maj.home / "current").unlink()
    os.symlink(apres_maj.home / snapshot.VENVS / "2026-08-01T00-00-00Z", apres_maj.home / "current")
    conn = sqlite3.connect(str(apres_maj.db_path))
    conn.execute(f"PRAGMA user_version = {schema.SCHEMA_VERSION + 1}")
    conn.close()
    snapshot.create(apres_maj)                       # la sûreté, prise après la MAJ : schéma N+1

    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path)

    assert "EN AVANT" in str(exc.value)
    assert "update apply" in str(exc.value), "le refus doit nommer le verbe qui va, lui, en avant"


# --- 6. l'unité de retour arrière est EXÉCUTOIRE ---------------------------------------------------------
#
# Les deux gestes — le lien ET les données — forment une seule unité. Ce que ces tests interdisent n'est pas
# l'échec : c'est la MOITIÉ. « Binaire rebasculé, données non restaurées » met un binaire ancien sur une base
# déjà migrée — et la base monte en forward-only, donc cette moitié-là est définitive. Chaque test porte un
# mode de panne NOMMÉ, parce qu'un test qui garde « le cas d'erreur » en général ne garde rien.

def _args_rollback(home: Path, tmp: Path, shim: Path, cible_venv: Path, snap: Path):
    return apply_update._parse([
        "--mode", "rollback", "--home", str(home), "--link", str(home / "current"),
        "--target-venv", str(cible_venv), "--snapshot", str(snap),
        "--run-dir", str(tmp / "run"), "--base-url", "http://127.0.0.1:1",
        "--systemctl", str(shim), "--timeout", "1"])


@pytest.fixture
def shim(tmp_path: Path) -> Path:
    """Un `systemctl` qui note ce qu'on lui demande — on ne pilote pas le systemd de la machine de test."""
    trace = tmp_path / "systemctl.trace"
    faux = tmp_path / "systemctl"
    faux.write_text(f"#!/bin/sh\necho \"$@\" >> {trace}\n", encoding="utf-8")
    faux.chmod(0o755)
    return faux


def test_si_la_RESTAURATION_echoue_le_lien_est_RE_bascule_en_avant(
        apres_maj: Settings, tmp_path: Path, shim: Path, monkeypatch):
    """MODE DE PANNE 1 — le seul état que l'invariant interdit. Le lien vient de passer sur l'ancien binaire
    et les données n'ont pas suivi : la base porte l'état NEUF, déjà migré, et l'ancien binaire ne sait pas
    la lire. Forward-only : rien ne rattraperait ça. On re-bascule donc EN AVANT — le binaire neuf, lui,
    sait lire cette base."""
    avant = (apres_maj.home / "current").resolve()
    snap = next(iter(snapshot.snapshots_dir(apres_maj).iterdir()))
    cible = apres_maj.home / snapshot.VENVS / "2026-08-01T00-00-00Z"
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *_a, **_k: {"version": "0.1.0", "sha": "abc"})
    monkeypatch.setattr(apply_update, "take_snapshot", lambda *_a, **_k: snap)
    monkeypatch.setattr(apply_update, "_restore", lambda *_a, **_k: False)      # LA panne

    rc, verdict, details = apply_update.rollback(
        _args_rollback(apres_maj.home, tmp_path, shim, cible, snap), lambda _m: None)

    assert rc == 1
    assert (apres_maj.home / "current").resolve() == avant, "l'instance est restée sur l'ancien binaire"
    assert "RE-basculé" in verdict
    assert "Aucune moitié" in verdict


def test_si_la_BASCULE_echoue_aucune_restauration_nest_tentee(
        apres_maj: Settings, tmp_path: Path, shim: Path, monkeypatch):
    """MODE DE PANNE 2, le symétrique. La première moitié n'a pas eu lieu : on n'entame pas la seconde.
    Restaurer des données sous un binaire qu'on n'a pas pu changer, c'est fabriquer l'autre moitié."""
    snap = next(iter(snapshot.snapshots_dir(apres_maj).iterdir()))
    cible = apres_maj.home / snapshot.VENVS / "2026-08-01T00-00-00Z"
    restaure: list[Path] = []
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *_a, **_k: {"version": "0.1.0", "sha": "abc"})
    monkeypatch.setattr(apply_update, "take_snapshot", lambda *_a, **_k: snap)
    monkeypatch.setattr(apply_update, "_restore", lambda s, *_a, **_k: restaure.append(s) or True)

    def _swap_casse(link, target):
        raise OSError("disque en lecture seule")
    monkeypatch.setattr(apply_update, "swap", _swap_casse)

    rc, verdict, _ = apply_update.rollback(
        _args_rollback(apres_maj.home, tmp_path, shim, cible, snap), lambda _m: None)

    assert rc == 1
    assert restaure == [], "une restauration a été tentée alors que le lien n'avait pas bougé"
    assert "AUCUNE restauration" in verdict


def test_lORDRE_des_deux_gestes_est_fige_DANS_LE_CODE(tmp_path: Path):
    """MODE DE PANNE 3 — celui de demain, pas d'aujourd'hui. L'ordre `swap` PUIS `_restore` est contraint :
    `restore` interroge `<home>/current` pour savoir quel schéma le binaire en place lit ; inversé, il verrait
    le binaire NEUF et refuserait une restauration pourtant légitime. Un mock ne garde que l'appel du jour ;
    ce test LIT le code, comme le garde AST des `Popen` — parce que la « simplification » qui échangera deux
    lignes ne se verra pas à la relecture, et que le symptôme qu'elle produirait (un refus de compatibilité)
    ne ressemble pas à sa cause."""
    source = Path(apply_update.__file__).read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(source))
              if isinstance(n, ast.FunctionDef) and n.name == "rollback")

    # `ast.walk` parcourt en LARGEUR : son ordre n'est pas celui du source. On trie sur `lineno`, sans quoi
    # ce test dirait quelque chose d'autre que ce qu'il prétend — et le dirait par hasard.
    gestes = sorted((n.lineno, n.func.id) for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in ("swap", "_restore"))
    premier_couple = gestes[:2]

    assert [nom for _l, nom in premier_couple] == ["swap", "_restore"], (
        f"l'ordre des deux gestes a changé dans `rollback` : {premier_couple}. Le lien D'ABORD, la "
        f"restauration ENSUITE — sans quoi le garde de compatibilité voit le mauvais binaire.")


def test_une_cible_qui_ne_ramene_pas_est_refusee_AVANT_TOUT_EFFET(
        apres_maj: Settings, tmp_path: Path, capsys):
    """MODE DE PANNE 4. Le refus est déjà porté par le préflight ; ce qui est gardé ici est qu'il tombe
    AVANT le premier geste — lien intact, aucun ordre donné au service. Un refus tardif laisse une instance
    dans un état que personne n'a choisi."""
    from forgemaster.cli import main

    ancien = apres_maj.home / snapshot.VENVS / "2026-08-01T00-00-00Z"
    (ancien / "bin" / "python").write_text(f"#!/bin/sh\necho {schema.SCHEMA_VERSION + 5}\n", encoding="utf-8")
    avant = (apres_maj.home / "current").resolve()

    rc = main(["update", "rollback", "--unit", str(_unite(apres_maj, tmp_path)),
               "--home", str(apres_maj.home), "--projects-root", str(apres_maj.projects_root)])

    assert rc == 1
    assert "restaurable" in capsys.readouterr().err
    assert (apres_maj.home / "current").resolve() == avant
    assert not (apres_maj.home / "updates").exists(), "un dossier de run a été créé : le geste avait commencé"


def _instantane_dont_le_restore_sort_en(code: int, tmp: Path) -> Path:
    """Un instantané réduit à ce que `_restore` lance : sa copie FIGÉE de `restore.py`. C'est bien celle-là
    qui est jouée (l'invariant « le script voyage »), donc c'est elle qu'on fait échouer."""
    dossier = tmp / f"snap-rc{code}"
    dossier.mkdir()
    (dossier / "restore.py").write_text(f"import sys\nsys.exit({code})\n", encoding="utf-8")
    return dossier


def test__restore_RAPPORTE_un_echec_reel_du_script_fige(tmp_path: Path):
    """La couture que les autres tests sautent : ils mockent `_restore`, donc aucun ne prouve qu'un rc non
    nul du VRAI script est bien rapporté. Sans ce test, faire rendre `True` à l'échec passait inaperçu —
    mesuré par mutation, pas supposé. Toute la compensation repose sur ce booléen."""
    assert apply_update._restore(_instantane_dont_le_restore_sort_en(1, tmp_path),
                                 tmp_path / "home", lambda _m: None) is False


def test__restore_RAPPORTE_un_succes_reel(tmp_path: Path):
    """Le symétrique, sans quoi « rendre toujours False » passerait : la compensation partirait à chaque
    retour arrière RÉUSSI, ce qui le défait aussitôt."""
    assert apply_update._restore(_instantane_dont_le_restore_sort_en(0, tmp_path),
                                 tmp_path / "home", lambda _m: None) is True


def test_revenir_vers_un_wheel_SANS_contrat_dinterface_reste_possible(
        apres_maj: Settings, tmp_path: Path, shim: Path, monkeypatch):
    """LE CONTRE-TÉMOIN de l'asymétrie posée par le détecteur de panne (2026-08-07) : *on exige la preuve
    pour AVANCER, jamais pour REVENIR*. Toute cible un peu ancienne est un wheel antérieur à la vérification
    d'interface — elle ne déclare aucun contrat. Si son absence bloquait, le retour arrière deviendrait
    impossible exactement au moment où il sert : après une MAJ qui a mal tourné."""
    snap = next(iter(snapshot.snapshots_dir(apres_maj).iterdir()))
    cible = apres_maj.home / snapshot.VENVS / "2026-08-01T00-00-00Z"
    monkeypatch.setattr(apply_update, "take_snapshot", lambda *_a, **_k: snap)
    monkeypatch.setattr(apply_update, "_restore", lambda *_a, **_k: True)
    # La cible est un venv de test : son `bin/python` n'existe pas, donc `package_dir` rend None — c'est
    # LITTÉRALEMENT le cas d'un wheel sans contrat, obtenu sans le simuler.
    monkeypatch.setattr(apply_update, "_wait_health", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(apply_update, "_get_json", lambda *_a, **_k: {"version": "0.1.0", "sha": "abc"})
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *_a, **_k: {"version": "0.1.0", "sha": "abc"})

    rc, verdict, details = apply_update.rollback(
        _args_rollback(apres_maj.home, tmp_path, shim, cible, snap), lambda _m: None)

    assert rc == 0, verdict
    assert "ne déclare pas de contrat d'interface" in verdict, "la dégradation doit être DITE, pas tue"
    assert details["impact"] == "revenu à l'état de l'instantané (venv + données)"


# --- 7. l'APTITUDE — la même question, posée AVANT qu'on demande le geste --------------------------------
#
# Elle ne lève jamais : c'est ce qui la rend lisible par une surface au repos. Ces tests gardent donc
# d'abord CETTE propriété, puis la frontière qui la définit (structurel ≠ transitoire), puis le couplage —
# une troisième marche qui répondrait autrement que le verbe ré-ouvrirait exactement le défaut de 2a‴.

def _aptitude(settings: Settings, tmp: Path, **over):
    return update.aptitude(settings, unit=str(_unite(settings, tmp)), scope="user", **over)


def test_l_aptitude_ne_LEVE_JAMAIS_sur_les_refus_de_SOCLE(apres_maj: Settings, tmp_path: Path,
                                                          monkeypatch):
    """Chacun des refus structurels rend un ÉTAT avec son texte, et aucun ne remonte d'exception. C'est la
    propriété qui autorise une surface à lire cette réponse **au repos** : si un seul cas levait, le
    panneau devrait traiter un état normal du produit comme une panne."""
    unite = _unite(apres_maj, tmp_path)

    # ① l'unité lance un venv EN DUR — LE cas de la fiche : une instance jamais migrée vers le lien stable
    dur = apres_maj.home / snapshot.VENVS / "2026-08-01T00-00-00Z" / "bin" / "forgemaster"
    unite.write_text(f"[Service]\nExecStart={dur} serve --host 127.0.0.1 --port 8700\n", encoding="utf-8")
    vue = update.aptitude(apres_maj, unit=str(unite), scope="user")
    assert vue["deployable"]["ok"] is False
    assert "un venv EN DUR" in vue["deployable"]["reason"]
    assert "install-service" in vue["deployable"]["reason"], "le refus porte DÉJÀ sa réparation"

    # ② aucune unité du tout
    vue = update.aptitude(apres_maj, unit=str(tmp_path / "absente.service"), scope="user")
    assert vue["deployable"]["ok"] is False and "aucune unité systemd" in vue["deployable"]["reason"]

    # ③ pas de lien stable
    (apres_maj.home / "current").unlink()
    vue = _aptitude(apres_maj, tmp_path)
    assert vue["deployable"]["ok"] is False and "lien stable" in vue["deployable"]["reason"]

    # ④ le lanceur manque — sans lui l'applicateur mourrait dans le cgroup de son lanceur
    monkeypatch.setattr(update.shutil, "which", lambda _n: None)
    vue = _aptitude(apres_maj, tmp_path)
    assert vue["deployable"]["ok"] is False and update.RUNNER in vue["deployable"]["reason"]


def test_un_SOCLE_refuse_rend_la_reversibilite_INDETERMINEE_et_pas_FAUSSE(apres_maj: Settings,
                                                                         tmp_path: Path):
    """`None`, jamais `False`. Le venv courant vient du socle ; quand le socle refuse, on n'a mesuré aucun
    retour — et « je n'ai pas pu mesurer » n'est pas « non ». Le module tient déjà cet idiome ailleurs
    (`impact: null`, l'état `unknown`, `python_schema` qui rend `None`), et il a été ajouté pour ça.

    Conséquence de surface, qui est le vrai enjeu : la page affiche UN refus, pas deux."""
    vue = update.aptitude(apres_maj, unit=str(tmp_path / "absente.service"), scope="user")

    assert vue["reversible"]["ok"] is None, "un socle refusé ne rend pas le retour IMPOSSIBLE, il le rend "\
                                            "non mesurable"
    assert vue["reversible"]["target"] is None
    assert "indéterminé" in vue["reversible"]["reason"]


def test_le_TRANSITOIRE_ne_change_PAS_l_aptitude_et_son_contre_temoin_le_prouve(apres_maj: Settings,
                                                                               tmp_path: Path):
    """LA frontière de cette phase. Un dispatch en vol et du travail non commité refusent le GESTE — ils ne
    disent rien de ce que l'instance SAIT faire. Les afficher au repos comme une aptitude serait un mensonge
    d'une autre espèce, et il vieillirait en secondes sur une page qu'on ne relit pas.

    Le contre-témoin est la moitié qui compte : sans lui, ce test passerait aussi si les deux refus avaient
    simplement cessé d'exister."""
    jobs = [{"project": "atelier-fictif", "feature": "f", "task": "t", "job_id": 7,
             "started_at": "2026-08-07T00:00:00Z"}]
    sale = [{"slug": "atelier-fictif", "state": update.auth.BLOCKING, "detail": "2 fichiers non commités"}]

    vue = _aptitude(apres_maj, tmp_path)
    assert vue["deployable"]["ok"] is True and vue["reversible"]["ok"] is True

    # contre-témoin : les MÊMES entrées, sur le verbe, refusent bel et bien
    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path, in_flight=jobs)
    assert "dispatch en cours" in str(exc.value)
    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path, authority=sale)
    assert "NON COMMITÉ" in str(exc.value)


def test_les_TROIS_marches_designent_la_MEME_cible(apres_maj: Settings, tmp_path: Path):
    """L'acquis de 2a‴, re-tenu à l'arrivée d'un troisième lecteur. `snapshot list` (l'état), le verbe (la
    résolution) et l'aptitude (l'annonce au repos) consultent le même `_cible_utilisable` — sinon la
    surface promettrait un retour que le verbe ne ferait pas, ce qui est pire que ne rien promettre."""
    vue = _aptitude(apres_maj, tmp_path)
    plan = _plan(apres_maj, tmp_path)

    assert vue["reversible"]["ok"] is True
    assert vue["reversible"]["target"]["venv"] == str(plan["target_venv"])
    assert vue["reversible"]["target"]["snapshot"] == plan["snapshot_name"]
    (lu,) = [s for s in snapshot.list_snapshots(apres_maj)
             if s["name"] == vue["reversible"]["target"]["snapshot"]]
    assert lu["state"] == "restaurable"


def test_apres_une_MAJ_NON_MIGRANTE_l_aptitude_nomme_le_venv_D_AVANT(apres_maj: Settings, tmp_path: Path):
    """La 5a et la 5b se prouvent l'une l'autre : sans le départage, l'aptitude annoncerait au repos, en
    permanence et sur toute instance à une MAJ non migrante d'écart, qu'elle ne sait pas revenir. C'est
    exactement le faux négatif que la coupe 5a/5b existe pour ne pas industrialiser."""
    snap, ancien, _ = _maj_non_migrante(apres_maj)

    vue = _aptitude(apres_maj, tmp_path)

    assert vue["reversible"]["ok"] is True
    assert vue["reversible"]["target"]["venv"] == str(ancien)
    assert vue["reversible"]["target"]["path"] == str(snap)


def test_sans_aucun_instantane_l_aptitude_dit_NON_avec_LE_MOTIF_DU_VERBE(apres_maj: Settings,
                                                                        tmp_path: Path):
    """Une phrase, deux lecteurs. Le verbe lève ce motif, l'aptitude le rend — les laisser diverger ferait
    lire deux diagnostics différents sur une seule instance, ce qui est le défaut que `_preflight_service`
    a été extrait pour empêcher côté socle."""
    for d in snapshot.snapshots_dir(apres_maj).iterdir():
        for f in sorted(d.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
        d.rmdir()

    vue = _aptitude(apres_maj, tmp_path)
    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path)

    assert vue["deployable"]["ok"] is True, "l'instance sait se DÉPLOYER : c'est le retour qui n'a pas de "\
                                            "cible"
    assert vue["reversible"]["ok"] is False
    assert vue["reversible"]["reason"] == str(exc.value)


def test_le_verbe_aptitude_rend_TOUJOURS_0_meme_quand_tout_refuse(apres_maj: Settings, tmp_path: Path,
                                                                  capsys):
    """La parité CLI du 200 de la route : lire un état n'est pas une panne. Ce qui sort en rc 1, c'est un
    GESTE refusé — `apply`, `rollback` — jamais une question."""
    args = _args_aptitude(unit=str(tmp_path / "absente.service"))
    assert update.cli_dispatch(apres_maj, args) == 0
    sortie = capsys.readouterr().out
    assert "✗ déployable" in sortie
    assert "? réversible" in sortie, "le socle refusé rend un `?`, pas une croix : rien n'a été mesuré"

    args = _args_aptitude(unit=str(_unite(apres_maj, tmp_path)))
    assert update.cli_dispatch(apres_maj, args) == 0
    sortie = capsys.readouterr().out
    assert "✓ réversible — vers " in sortie and "2026-08-01T00-00-00Z" in sortie
    assert "DISPONIBILITÉ" in sortie, "la frontière se dit à l'utilisateur, pas seulement au code"


def _args_aptitude(*, unit: str):
    import argparse
    return argparse.Namespace(action="aptitude", unit=unit, system=False)


def test_l_aptitude_ne_DESIGNE_JAMAIS_ce_que_le_verbe_REFUSE(apres_maj: Settings, tmp_path: Path):
    """Le CONTRE-TÉMOIN du couplage, sur le seul décor où une aptitude naïve divergerait — et le test
    précédent ne l'attrape pas, parce que chez lui « le premier restaurable » et la bonne réponse tombent
    d'accord.

    Après un retour, la prise de SÛRETÉ est l'instantané le plus récent **et** `restaurable`. Une aptitude
    qui prendrait le premier de la liste l'annoncerait au repos comme la cible du prochain retour, pendant
    que le verbe la refuse — et cette fois le mensonge serait affiché en permanence, sans qu'on ait cliqué.
    C'est exactement la raison pour laquelle le parcours est extrait au lieu d'être ré-écrit."""
    _, ancien, neuf = _maj_non_migrante(apres_maj)
    surete = snapshot.create(apres_maj)
    (apres_maj.home / "current").unlink()
    os.symlink(ancien, apres_maj.home / "current")
    run = apres_maj.home / "updates" / "2026-08-10T00-00-00Z"
    run.mkdir(parents=True)
    (run / "result.json").write_text(
        json.dumps({"rc": 0, "mode": "rollback", "venv_avant": str(neuf),
                    "instantane_surete": str(surete)}), encoding="utf-8")

    vue = _aptitude(apres_maj, tmp_path)
    with pytest.raises(UpdateRefused) as exc:
        _plan(apres_maj, tmp_path)

    # la liste la marque `restaurable` : c'est bien le décor où le naïf se tromperait
    (lue,) = [s for s in snapshot.list_snapshots(apres_maj) if s["path"] == str(surete)]
    assert lue["state"] == "restaurable"
    assert vue["reversible"]["ok"] is False, "aucune cible ne ramène en arrière — et la surface doit le dire"
    assert vue["reversible"]["target"] is None, "elle ne DÉSIGNE surtout pas la prise de sûreté"
    assert vue["reversible"]["reason"] == str(exc.value), "un seul parcours, donc un seul motif"
