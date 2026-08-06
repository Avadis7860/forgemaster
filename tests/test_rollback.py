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

    class _Popen:
        def __init__(self, cmd, **_kw):
            vus["cmd"] = cmd

    # Le plan se résout AVANT le monkeypatch : la résolution sonde les venvs par `subprocess.run`, qui
    # passe par le même `Popen`. Patcher d'abord ferait échouer la sonde et non le lanceur.
    plan = _plan(apres_maj, tmp_path)
    monkeypatch.setattr(update.subprocess, "Popen", _Popen)

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

    gestes = [(n.lineno, n.func.id) for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id in ("swap", "_restore")]
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
