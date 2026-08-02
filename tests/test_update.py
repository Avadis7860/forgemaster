"""Tests d'`update` + `apply_update` — poser un wheel en bleu/vert, et REVENIR TOUT SEUL.

Ce que ces tests gardent, dans l'ordre d'importance :

1. **le retour arrière est automatique et complet** — quand le vivant ne sert pas la version posée, le lien
   rebascule ET l'instantané est restauré, sans qu'on ait rien demandé (le `restore.py` réellement joué est
   celui figé dans l'instantané, pas un jumeau de test) ;
2. **un échec avant la bascule ne touche à rien** — pas un `systemctl`, pas un lien déplacé. C'est ce qui
   rend la MAJ tentable : au pire, il ne s'est rien passé ;
3. **les refus sont fail-closed et actionnables** — une unité qui lance un venv en dur n'est pas « cassée »,
   elle est non migrée, et le message dit la commande unique qui la migre.

La chaîne réelle (vrai wheel, vrai venv, vrai daemon, vrai systemd-like) est jouée par
`deploy/acceptance-update-rollback.sh` : ici on garde les DÉCISIONS, là-bas le geste complet.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

from cockpit import apply_update, service, snapshot, update
from cockpit.config import Settings
from cockpit.db import store
from cockpit.update import UpdateRefused

_INSERT = "INSERT INTO projects (id, slug, name, sot_path, created_at) VALUES (?, ?, ?, ?, ?)"


# --- décors -------------------------------------------------------------------------------------------

@pytest.fixture
def live(tmp_path: Path) -> Settings:
    """Une instance installée : base peuplée, réglages, et un lien stable vers un « venv » courant."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    conn.execute(_INSERT, ("id-a", "atelier-fictif", "atelier-fictif", "/x.git", "2026-08-02T00:00:00Z"))
    conn.commit()
    conn.close()
    (settings.home / "cockpit.env").write_text("COCKPIT_SECRET_STORE=file\n", encoding="utf-8")
    venv = tmp_path / "venvs" / "avant"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "cockpit").write_text("#!/bin/sh\nexit 0\n")
    os.symlink(venv, settings.home / "current")
    return settings


def _unit(path: Path, exec_bin: str, *, host: str = "127.0.0.1", port: int = 8700) -> Path:
    path.write_text(f"[Service]\nExecStart={exec_bin} serve --host {host} --port {port}\n", encoding="utf-8")
    return path


def _systemctl_shim(tmp: Path) -> tuple[Path, Path]:
    """Un faux `systemctl` qui NOTE ce qu'on lui demande. On ne pilote pas le systemd de la machine de test
    (effet système) ; ce qui est vérifié ici, c'est la SÉQUENCE d'ordres, qui est la partie fragile."""
    trace = tmp / "systemctl.trace"
    shim = tmp / "systemctl"
    shim.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys, pathlib
        pathlib.Path({str(trace)!r}).open("a").write(" ".join(sys.argv[1:]) + "\\n")
    """), encoding="utf-8")
    shim.chmod(0o755)
    return shim, trace


def _args(live: Settings, tmp: Path, shim: Path, **over) -> object:
    base = ["--wheel", str(tmp / "faux.whl"), "--home", str(live.home),
            "--link", str(live.home / "current"), "--venvs", str(tmp / "venvs"),
            "--run-dir", str(tmp / "run"), "--base-url", "http://127.0.0.1:1",
            "--systemctl", str(shim), "--timeout", "1"]
    for key, val in over.items():
        base += [f"--{key.replace('_', '-')}", str(val)]
    return apply_update._parse(base)


def _trace(path: Path) -> list[str]:
    """Les ordres donnés au service, dans l'ordre — la portée (`--user`) est du bruit ici."""
    mots = path.read_text(encoding="utf-8").split() if path.exists() else []
    return [m for m in mots if m in ("stop", "start", "restart")]


# --- 1. le retour arrière -----------------------------------------------------------------------------

def test_le_vivant_qui_ne_sert_pas_fait_rebasculer_le_lien_ET_restaurer_linstantane(
        live: Settings, tmp_path: Path, monkeypatch):
    """LE test du module. La nouvelle version passe la sonde en isolation (home vierge) puis échoue EN
    VIVANT — exactement le cas d'une migration qui casse sur la vraie base. Les deux gestes du retour
    arrière doivent partir ensemble : rebasculer le lien ne suffit pas, la base a déjà été migrée."""
    shim, trace = _systemctl_shim(tmp_path)
    avant = (live.home / "current").resolve()
    neuf = tmp_path / "venvs" / "neuf"
    (neuf / "bin").mkdir(parents=True)

    dest = snapshot.create(live)                 # l'instantané que la MAJ prendrait à froid
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: neuf / "bin" / "cockpit")
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *a, **k: {"version": "9.9", "sha": "beef"})
    monkeypatch.setattr(apply_update, "take_snapshot", lambda *a, **k: dest)

    def _degat_de_migration(*_a, **_k):
        """La nouvelle version a migré la base avant de mourir — dégât REL, pas simulé à la marge."""
        conn = sqlite3.connect(str(live.db_path))
        conn.execute(_INSERT, ("id-b", "migre-a-moitie", "x", "/y.git", "2026-08-02T00:00:00Z"))
        conn.commit()
        conn.close()
        return False, "le daemon ne répond pas"

    monkeypatch.setattr(apply_update, "_verify_live", _degat_de_migration)
    monkeypatch.setattr(apply_update, "_wait_health", lambda *a, **k: True)   # il répond APRÈS le retour

    rc, verdict, details = apply_update.apply(_args(live, tmp_path, shim), lambda _m: None)

    assert rc == 1 and "revenue à l'état d'avant" in verdict
    assert (live.home / "current").resolve() == avant, "le lien n'est pas revenu au venv d'avant"
    assert _slugs(live.db_path) == ["atelier-fictif"], "la base n'a pas été restaurée : le dégât est resté"
    assert details["impact"].startswith("revenu à l'état d'avant")
    assert _trace(trace) == ["stop", "start", "stop", "start"]   # arrêt+bascule, puis arrêt+retour arrière


def test_le_retour_arriere_qui_ne_ramene_pas_le_service_le_DIT(live: Settings, tmp_path: Path, monkeypatch):
    """Un retour arrière qui échoue lui aussi doit sortir en rc 2 et pointer l'instantané intact — pas
    conclure au vert parce qu'il a « fait ce qu'il pouvait »."""
    shim, _trace_path = _systemctl_shim(tmp_path)
    dest = snapshot.create(live)
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: tmp_path / "x" / "bin" / "cockpit")
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *a, **k: {"version": "9.9", "sha": "beef"})
    monkeypatch.setattr(apply_update, "take_snapshot", lambda *a, **k: dest)
    monkeypatch.setattr(apply_update, "_verify_live", lambda *a, **k: (False, "muet"))
    monkeypatch.setattr(apply_update, "_wait_health", lambda *a, **k: False)

    rc, verdict, _d = apply_update.apply(_args(live, tmp_path, shim), lambda _m: None)
    assert rc == 2
    assert "ne répond TOUJOURS pas" in verdict and str(dest) in verdict


# --- 2. un échec avant la bascule ne touche à rien ------------------------------------------------------

def test_un_wheel_qui_ne_sert_pas_en_isolation_narrete_meme_pas_le_service(
        live: Settings, tmp_path: Path, monkeypatch):
    """La valeur du bleu/vert tient à cette propriété : tant que la nouvelle version n'a pas SERVI, le
    vivant n'a rien subi. Aucun `systemctl`, aucun lien déplacé — au pire, il ne s'est rien passé."""
    shim, trace = _systemctl_shim(tmp_path)
    avant = (live.home / "current").resolve()
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: tmp_path / "x" / "bin" / "cockpit")

    def _ne_sert_pas(*_a, **_k):
        raise apply_update.UpdateFailed("la nouvelle version ne sert pas en isolation.")

    monkeypatch.setattr(apply_update, "probe_isolated", _ne_sert_pas)
    rc, verdict, details = apply_update.apply(_args(live, tmp_path, shim), lambda _m: None)

    assert rc == 1 and verdict.startswith("MAJ refusée")
    assert details["impact"] == "aucun : le service n'a pas été touché"
    assert not trace.exists(), f"systemctl a été appelé alors que rien ne devait bouger : {_trace(trace)}"
    assert (live.home / "current").resolve() == avant


def test_sans_instantane_on_ne_bascule_pas_et_on_relance_le_service_tel_quel(
        live: Settings, tmp_path: Path, monkeypatch):
    """Un ancien cockpit qui ne sait pas prendre d'instantané fait échouer la MAJ ICI, service arrêté mais
    intact — jamais après la bascule. Basculer sans instantané, c'est rendre la MAJ irréversible (la base
    monte en forward-only), donc c'est le seul point où l'on refuse même si tout le reste était vert."""
    shim, trace = _systemctl_shim(tmp_path)
    avant = (live.home / "current").resolve()
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: tmp_path / "x" / "bin" / "cockpit")
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *a, **k: {"version": "9.9", "sha": "beef"})

    def _pas_de_verbe(*_a, **_k):
        raise apply_update.UpdateFailed("impossible de prendre l'instantané")

    monkeypatch.setattr(apply_update, "take_snapshot", _pas_de_verbe)
    rc, verdict, details = apply_update.apply(_args(live, tmp_path, shim), lambda _m: None)

    assert rc == 1 and "instantané" in verdict
    assert details["impact"] == "aucun : service relancé tel quel"
    assert _trace(trace) == ["stop", "start"]        # arrêté puis RELANCÉ tel quel, jamais laissé mort
    assert (live.home / "current").resolve() == avant


def test_la_bascule_du_lien_est_atomique_et_ne_passe_jamais_par_le_vide(tmp_path: Path):
    """`unlink` puis `symlink` laisserait une fenêtre où l'unité systemd pointe le néant. `os.replace` sur
    un lien temporaire ne l'a pas — et le lien reste valide même en rebasculant deux fois."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    link = tmp_path / "current"
    os.symlink(a, link)
    apply_update.swap(link, b)
    assert link.resolve() == b and link.is_symlink()
    apply_update.swap(link, a)
    assert link.resolve() == a
    assert not (tmp_path / "current.swap").exists(), "le temporaire de bascule n'a pas été consommé"


# --- 3. les refus, fail-closed et actionnables -----------------------------------------------------------

def test_refus_sans_unite_systemd(live: Settings, tmp_path: Path):
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    with pytest.raises(UpdateRefused, match="install-service"):
        update.preflight(live, wheel=str(whl), unit=str(tmp_path / "absente.service"), scope="user")


def test_refus_dune_unite_qui_lance_un_venv_en_dur(live: Settings, tmp_path: Path):
    """L'installation antérieure au bleu/vert n'est pas cassée, elle est NON MIGRÉE. Le message doit dire
    la commande unique qui la migre — sinon on laisse quelqu'un devant un « impossible » nu."""
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    unit = _unit(tmp_path / "cockpit.service", "/opt/venv-fige/bin/cockpit")
    with pytest.raises(UpdateRefused) as exc:
        update.preflight(live, wheel=str(whl), unit=str(unit), scope="user")
    assert "EN DUR" in str(exc.value) and "install-service" in str(exc.value)
    assert "daemon-reload" in str(exc.value)


def test_refus_sans_lien_stable(live: Settings, tmp_path: Path):
    (live.home / "current").unlink()
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    unit = _unit(tmp_path / "cockpit.service", str(live.home / "current" / "bin" / "cockpit"))
    with pytest.raises(UpdateRefused, match="lien stable"):
        update.preflight(live, wheel=str(whl), unit=str(unit), scope="user")


def test_refus_portee_systeme_sans_root(live: Settings, tmp_path: Path, monkeypatch):
    """`systemctl` échouerait au milieu, service arrêté. On refuse AVANT, pas au pire moment."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    with pytest.raises(UpdateRefused, match="root"):
        update.preflight(live, wheel=str(whl), unit=None, scope="system")


def test_refus_dun_fichier_qui_nest_pas_un_wheel(live: Settings, tmp_path: Path):
    pas_un_wheel = tmp_path / "cockpit.tar.gz"
    pas_un_wheel.write_bytes(b"")
    with pytest.raises(UpdateRefused, match="wheel"):
        update.preflight(live, wheel=str(pas_un_wheel), unit=None, scope="user")
    with pytest.raises(UpdateRefused, match="introuvable"):
        update.preflight(live, wheel=str(tmp_path / "jamais.whl"), unit=None, scope="user")


def test_une_unite_sans_port_est_refusee_car_aucune_verification_ne_serait_possible(live, tmp_path: Path):
    """Sans port, pas de sonde en vivant — donc pas de retour arrière automatique. Le laisser passer
    livrerait une MAJ qui bascule et ne vérifie rien : exactement ce qu'on cherche à supprimer."""
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    unit = tmp_path / "cockpit.service"
    unit.write_text(f"[Service]\nExecStart={live.home}/current/bin/cockpit serve\n", encoding="utf-8")
    with pytest.raises(UpdateRefused, match="port"):
        update.preflight(live, wheel=str(whl), unit=str(unit), scope="user")


def test_le_plan_sonde_la_boucle_locale_meme_quand_le_service_ecoute_partout(live: Settings, tmp_path: Path):
    """`--host 0.0.0.0` est un bind, pas une adresse joignable. Sonder `http://0.0.0.0:…` échouerait sur
    certaines piles et ferait conclure à tort à une MAJ ratée — donc on sonde la boucle locale."""
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    unit = _unit(tmp_path / "cockpit.service", str(live.home / "current" / "bin" / "cockpit"),
                 host="0.0.0.0", port=8712)
    plan = update.preflight(live, wheel=str(whl), unit=str(unit), scope="user")
    assert plan["base_url"] == "http://127.0.0.1:8712"
    assert plan["venv"] == (live.home / "current").resolve()


def test_parse_exec_start_prend_la_derniere_ligne_et_ignore_les_prefixes_systemd():
    """systemd autorise `ExecStart=-/chemin` (échec toléré) et plusieurs lignes, la dernière gagnant."""
    binaire, host, port = update.parse_exec_start(
        "ExecStart=\nExecStart=-/o/current/bin/cockpit serve --host 0.0.0.0 --port 9001\n")
    assert (binaire, host, port) == ("/o/current/bin/cockpit", "0.0.0.0", 9001)
    with pytest.raises(UpdateRefused, match="ExecStart"):
        update.parse_exec_start("[Service]\nType=simple\n")


# --- la vérification en vivant --------------------------------------------------------------------------

def test_un_build_non_tamponne_ne_conclut_pas_au_vert_en_silence():
    """Un wheel sans tampon de provenance (`sha=None`, checkout éditable) ne se compare pas. On accepte la
    version seule, mais on le DIT : un vert muet ferait croire que la provenance a été vérifiée."""
    ok, why = apply_update.matches({"version": "0.1.0", "sha": None}, {"version": "0.1.0", "sha": None})
    assert ok and "non comparable" in why


def test_le_vivant_qui_sert_un_autre_build_est_un_echec():
    """Le cas qui compte : le service a bien redémarré, il répond — mais c'est l'ANCIEN binaire (unité non
    rechargée, lien non pris en compte). Sans cette comparaison, la MAJ se déclarerait posée sans l'être."""
    ok, why = apply_update.matches({"version": "0.2.0", "sha": "a" * 40},
                                   {"version": "0.2.0", "sha": "b" * 40})
    assert not ok and "attendu aaaa" in why
    ok, why = apply_update.matches({"version": "0.2.0", "sha": "a" * 40},
                                   {"version": "0.1.0", "sha": "a" * 40})
    assert not ok and "0.1.0" in why


# --- la prise, déléguée au cockpit ANCIEN --------------------------------------------------------------

def test_la_prise_passe_par_la_VRAIE_ligne_de_commande_du_cockpit_installe(live: Settings, tmp_path: Path):
    """Test de contact, pas de forme : on lance la vraie commande `cockpit` du venv courant et on exige un
    instantané réel. La première version passait `--home` AVANT la sous-commande — argparse le porte sur la
    SOUS-commande, donc la MAJ mourait à l'étape de l'instantané. Aucune relecture ne l'avait vu ; seul un
    appel réel le montre, et c'est le genre de bug qu'un `monkeypatch` de `subprocess.run` cache pour de
    bon."""
    console = Path(sys.executable).with_name("cockpit")
    if not console.is_file():
        pytest.skip(f"pas de commande `cockpit` à côté de {sys.executable} — rien à mettre en contact")
    dest = apply_update.take_snapshot(console, live.home, lambda _m: None)
    assert (dest / snapshot.MANIFEST).is_file()
    assert dest.parent == live.home / "snapshots"


def test_un_ancien_cockpit_qui_ignore_le_verbe_snapshot_fait_echouer_la_MAJ(live: Settings, tmp_path: Path):
    """On ne bascule jamais sur une version dont on ne saurait pas revenir : si la prise échoue, la MAJ
    s'arrête là, et le message dit avec QUEL binaire elle a échoué."""
    vieux = tmp_path / "vieux-cockpit"
    vieux.write_text("#!/bin/sh\necho 'usage: cockpit' >&2\nexit 2\n", encoding="utf-8")
    vieux.chmod(0o755)
    with pytest.raises(apply_update.UpdateFailed, match="MAJ annulée"):
        apply_update.take_snapshot(vieux, live.home, lambda _m: None)


# --- le script autonome ------------------------------------------------------------------------------

def test_apply_ne_depend_de_rien_du_cockpit():
    """Même exigence que `restore.py`, pour la même raison : ce script tourne pendant qu'on remplace le venv
    du cockpit, sous le `python3` du système. Vérifié par AST — une régression d'import ne se voit pas à la
    relecture six mois plus tard."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(Path(apply_update.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "import relatif : le script ne tournerait plus hors du paquet"
            modules.add((node.module or "").split(".")[0])
    assert "cockpit" not in modules
    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)


def test_le_verbe_copie_le_script_au_lieu_de_le_lancer_depuis_le_paquet(live: Settings, tmp_path: Path,
                                                                       monkeypatch):
    """Le script doit survivre au venv qu'il remplace : on le copie dans le dossier de run avant de le
    lancer. Le lancer depuis `site-packages` reviendrait à scier la branche pendant qu'on est dessus."""
    lances: list[list[str]] = []
    monkeypatch.setattr(update.subprocess, "Popen",
                        lambda cmd, **_k: lances.append(cmd) or _FauxProc())
    plan = {"wheel": tmp_path / "c.whl", "home": live.home, "link": live.home / "current",
            "base_url": "http://127.0.0.1:8700", "scope": "user",
            "unit": tmp_path / "u", "venv": Path("/x")}
    monkeypatch.setattr(update, "follow", lambda run_dir, **_k: 0)
    assert update.launch(live, plan, systemctl="systemctl", service="cockpit", detach=False) == 0

    script = Path(lances[0][1])
    assert script.name == "apply.py" and script.parent.parent == live.home / "updates"
    assert script.read_text(encoding="utf-8") == Path(apply_update.__file__).read_text(encoding="utf-8")
    assert os.access(script, os.X_OK)


def test_le_suivi_interrompu_ne_conclut_pas_a_lechec(tmp_path: Path, capsys):
    """Un suivi qui expire dit où regarder ; il ne déclare pas la MAJ ratée — le script, lui, continue."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "journal.log").write_text("== en cours\n", encoding="utf-8")
    assert update.follow(run, timeout=0.05, poll=0.01) == 2
    assert "continue en arrière-plan" in capsys.readouterr().out


def test_le_suivi_rend_le_code_de_sortie_du_script(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "journal.log").write_text("== fini\n", encoding="utf-8")
    (run / "result.json").write_text(json.dumps({"rc": 1, "verdict": "revenue"}), encoding="utf-8")
    assert update.follow(run, timeout=2, poll=0.01) == 1


# --- le lien stable, côté installation --------------------------------------------------------------

def test_install_service_pose_le_lien_et_fait_pointer_lunite_dessus(tmp_path: Path, monkeypatch):
    """La migration d'une installation existante tient en une commande : relancer `install-service`. C'est
    ce que le refus d'`update apply` demande — il faut donc que ça suffise VRAIMENT."""
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    unit, _env, _hint = service.install_service(settings, host="127.0.0.1", port=8700, scope="user")
    link = service.stable_link(settings)
    assert link.is_symlink() and link.resolve() == Path(sys.prefix).resolve()
    assert f"ExecStart={link}/bin/cockpit serve" in unit.read_text(encoding="utf-8")


def test_le_venv_et_le_lien_sont_dits_hors_perimetre_de_linstantane(live: Settings):
    """Un instantané couvre la DONNÉE, pas le binaire — c'est pour ça que le retour arrière demande deux
    gestes. L'exclusion voyage dans le manifeste pour que la frontière soit lisible dans l'artefact."""
    dest = snapshot.create(live)
    exclus = json.loads((dest / snapshot.MANIFEST).read_text(encoding="utf-8"))["excluded"]
    assert {"venvs/", "current", "updates/"} <= set(exclus)


class _FauxProc:
    pid = 4242


def _slugs(db: Path) -> list[str]:
    conn = sqlite3.connect(str(db))
    try:
        return sorted(r[0] for r in conn.execute("SELECT slug FROM projects"))
    finally:
        conn.close()
