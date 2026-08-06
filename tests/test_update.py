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
import io
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from forgemaster import apply_update, service, snapshot, update
from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.update import UpdateRefused

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
    (settings.home / "forgemaster.env").write_text("FORGEMASTER_SECRET_STORE=file\n", encoding="utf-8")
    venv = tmp_path / "venvs" / "avant"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "forgemaster").write_text("#!/bin/sh\nexit 0\n")
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
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: neuf / "bin" / "forgemaster")
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
    monkeypatch.setattr(apply_update, "_wait_health", lambda *a, **k: (True, ""))  # il sert APRÈS le retour

    rc, verdict, details = apply_update.apply(_args(live, tmp_path, shim), lambda _m: None)

    assert rc == 1 and "revenue à l'état d'avant" in verdict
    assert (live.home / "current").resolve() == avant, "le lien n'est pas revenu au venv d'avant"
    assert _slugs(live.db_path) == ["atelier-fictif"], "la base n'a pas été restaurée : le dégât est resté"
    assert details["impact"].startswith("revenu à l'état d'avant")
    assert _trace(trace) == ["stop", "start", "stop", "start"]   # arrêt+bascule, puis arrêt+retour arrière


def test_une_instance_QUI_SE_DIT_INSERVABLE_declenche_le_retour_arriere_automatique(
        live: Settings, tmp_path: Path, monkeypatch):
    """LE critère de la phase 2a″ — le reste n'est que de la plomberie de sonde.

    Les autres tests du retour arrière remplacent `_verify_live` par un mock : ils prouvent que le retour
    part **quand on lui dit** de partir. Ici on ne mocke que la SONDE HTTP, et on vérifie que la chaîne
    entière conclut toute seule : `/health` 503 `ready:false` → `_wait_health` → `_verify_live` → retour
    arrière. C'est exactement le chemin qui passait au VERT avant cette phase, sur une instance dont la
    base est illisible — le cas nominal du retour arrière volontaire qui vient ensuite (binaire ancien,
    donnée neuve)."""
    import urllib.error
    import urllib.request
    from contextlib import contextmanager

    shim, trace = _systemctl_shim(tmp_path)
    avant = (live.home / "current").resolve()
    neuf = tmp_path / "venvs" / "neuf"
    (neuf / "bin").mkdir(parents=True)
    dest = snapshot.create(live)
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: neuf / "bin" / "forgemaster")
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *a, **k: {"version": "9.9", "sha": "beef"})
    monkeypatch.setattr(apply_update, "take_snapshot", lambda *a, **k: dest)

    appels = []

    class _Ok:
        status = 200
        def read(self): return b'{"status":"ok","ready":true}'

    @contextmanager
    def _urlopen(url, timeout=None):                     # noqa: ARG001
        appels.append(url)
        if len(appels) == 1:                             # la vérification en vivant, après la bascule
            corps = json.dumps({"status": "unservable", "ready": False,
                                "detail": "cette base porte le schéma 21"}).encode()
            raise urllib.error.HTTPError(url, 503, "unservable", {}, io.BytesIO(corps))
        yield _Ok()                                      # après le retour arrière, elle sert de nouveau

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    rc, verdict, details = apply_update.apply(_args(live, tmp_path, shim), lambda _m: None)

    assert rc == 1, "l'instance se disait inservable et la MAJ a conclu au succès"
    assert "schéma 21" in verdict, "le verdict ne dit pas POURQUOI — l'utilisateur n'a que le journal"
    assert (live.home / "current").resolve() == avant, "le retour arrière n'a pas rebasculé le lien"
    assert details["impact"].startswith("revenu à l'état d'avant")
    assert _trace(trace) == ["stop", "start", "stop", "start"]


def test_le_retour_arriere_qui_ne_ramene_pas_le_service_le_DIT(live: Settings, tmp_path: Path, monkeypatch):
    """Un retour arrière qui échoue lui aussi doit sortir en rc 2 et pointer l'instantané intact — pas
    conclure au vert parce qu'il a « fait ce qu'il pouvait »."""
    shim, _trace_path = _systemctl_shim(tmp_path)
    dest = snapshot.create(live)
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: tmp_path / "x" / "bin" / "forgemaster")
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *a, **k: {"version": "9.9", "sha": "beef"})
    monkeypatch.setattr(apply_update, "take_snapshot", lambda *a, **k: dest)
    monkeypatch.setattr(apply_update, "_verify_live", lambda *a, **k: (False, "muet"))
    monkeypatch.setattr(apply_update, "_wait_health", lambda *a, **k: (False, "muet lui aussi"))

    rc, verdict, _d = apply_update.apply(_args(live, tmp_path, shim), lambda _m: None)
    assert rc == 2
    assert "ne sert TOUJOURS pas" in verdict and "muet lui aussi" in verdict and str(dest) in verdict


# --- 2. un échec avant la bascule ne touche à rien ------------------------------------------------------

def test_un_wheel_qui_ne_sert_pas_en_isolation_narrete_meme_pas_le_service(
        live: Settings, tmp_path: Path, monkeypatch):
    """La valeur du bleu/vert tient à cette propriété : tant que la nouvelle version n'a pas SERVI, le
    vivant n'a rien subi. Aucun `systemctl`, aucun lien déplacé — au pire, il ne s'est rien passé."""
    shim, trace = _systemctl_shim(tmp_path)
    avant = (live.home / "current").resolve()
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: tmp_path / "x" / "bin" / "forgemaster")

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
    """Un ancien forgemaster qui ne sait pas prendre d'instantané fait échouer la MAJ ICI, service arrêté mais
    intact — jamais après la bascule. Basculer sans instantané, c'est rendre la MAJ irréversible (la base
    monte en forward-only), donc c'est le seul point où l'on refuse même si tout le reste était vert."""
    shim, trace = _systemctl_shim(tmp_path)
    avant = (live.home / "current").resolve()
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: tmp_path / "x" / "bin" / "forgemaster")
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
    unit = _unit(tmp_path / "forgemaster.service", "/opt/venv-fige/bin/forgemaster")
    with pytest.raises(UpdateRefused) as exc:
        update.preflight(live, wheel=str(whl), unit=str(unit), scope="user")
    assert "EN DUR" in str(exc.value) and "install-service" in str(exc.value)
    assert "daemon-reload" in str(exc.value)


def test_refus_sans_lien_stable(live: Settings, tmp_path: Path):
    (live.home / "current").unlink()
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    unit = _unit(tmp_path / "forgemaster.service", str(live.home / "current" / "bin" / "forgemaster"))
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
    pas_un_wheel = tmp_path / "forgemaster.tar.gz"
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
    unit = tmp_path / "forgemaster.service"
    unit.write_text(f"[Service]\nExecStart={live.home}/current/bin/forgemaster serve\n", encoding="utf-8")
    with pytest.raises(UpdateRefused, match="port"):
        update.preflight(live, wheel=str(whl), unit=str(unit), scope="user")


def test_le_plan_sonde_la_boucle_locale_meme_quand_le_service_ecoute_partout(live: Settings, tmp_path: Path):
    """`--host 0.0.0.0` est un bind, pas une adresse joignable. Sonder `http://0.0.0.0:…` échouerait sur
    certaines piles et ferait conclure à tort à une MAJ ratée — donc on sonde la boucle locale."""
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    unit = _unit(tmp_path / "forgemaster.service", str(live.home / "current" / "bin" / "forgemaster"),
                 host="0.0.0.0", port=8712)
    plan = update.preflight(live, wheel=str(whl), unit=str(unit), scope="user")
    assert plan["base_url"] == "http://127.0.0.1:8712"
    assert plan["venv"] == (live.home / "current").resolve()


def test_parse_exec_start_prend_la_derniere_ligne_et_ignore_les_prefixes_systemd():
    """systemd autorise `ExecStart=-/chemin` (échec toléré) et plusieurs lignes, la dernière gagnant."""
    binaire, host, port = update.parse_exec_start(
        "ExecStart=\nExecStart=-/o/current/bin/forgemaster serve --host 0.0.0.0 --port 9001\n")
    assert (binaire, host, port) == ("/o/current/bin/forgemaster", "0.0.0.0", 9001)
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


def _sonde_health(monkeypatch, reponses):
    """Fait répondre `/health` selon une liste d'issues consommée dans l'ordre : `int` = statut HTTP nu,
    `dict` = corps d'un 503 de readiness, `Exception` = connexion refusée. Le vrai `urlopen` n'est jamais
    appelé — on teste la LECTURE de la sonde, pas le réseau."""
    import urllib.error
    import urllib.request
    from contextlib import contextmanager

    restes = list(reponses)

    class _Reponse:
        def __init__(self, status): self.status = status
        def read(self): return b"{}"

    @contextmanager
    def _urlopen(url, timeout=None):                     # noqa: ARG001
        issue = restes.pop(0) if restes else restes_defaut
        if isinstance(issue, Exception):
            raise issue
        if isinstance(issue, dict):
            corps = json.dumps(issue).encode()
            raise urllib.error.HTTPError(url, 503, "unservable", {}, io.BytesIO(corps))
        yield _Reponse(issue)

    restes_defaut = urllib.error.URLError("refusée")
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return restes


def test_une_instance_qui_se_declare_inservable_est_un_echec_IMMEDIAT_avec_son_motif(monkeypatch):
    """LE test de cette phase. `/health` était une liveness : une instance dont la base est illisible
    répondait 200, donc `_verify_live` concluait au succès — et le retour arrière AUTOMATIQUE ne se
    déclenchait pas sur le seul mode de panne qui le concerne.

    Deux propriétés, pas une : le verdict est **négatif**, et il est rendu **sans attendre le délai**. Un
    échec qui ne sort qu'à l'expiration transforme une réponse claire en silence, et c'est ce silence qui
    remonterait à l'utilisateur comme diagnostic."""
    debut = time.monotonic()
    _sonde_health(monkeypatch, [{"ready": False, "detail": "schéma 21 > 20 · snapshot restore"}])

    ok, why = apply_update._wait_health("http://x", timeout=30.0)

    assert ok is False
    assert "schéma 21 > 20" in why and "snapshot restore" in why
    assert time.monotonic() - debut < 5.0, "il a attendu le délai au lieu de lire la réponse"


def test_un_503_qui_nest_pas_le_notre_ne_conclut_pas_a_notre_place(monkeypatch):
    """Un 503 de reverse-proxy, ou d'un autre service qui écouterait ce port, n'est pas un verdict de
    l'instance. On ne conclut que sur notre propre contrat (`ready:false`) — sinon la sonde deviendrait un
    check défaillant, rouge sur ce qui n'est même pas notre affaire."""
    _sonde_health(monkeypatch, [{"detail": "Service Unavailable"}, 200])

    ok, why = apply_update._wait_health("http://x", timeout=30.0)

    assert ok is True and why == ""


# --- la prise, déléguée au forgemaster ANCIEN --------------------------------------------------------------

def test_la_prise_passe_par_la_VRAIE_ligne_de_commande_du_forgemaster_installe(
        live: Settings, tmp_path: Path):
    """Test de contact, pas de forme : on lance la vraie commande `forgemaster` du venv courant et on exige un
    instantané réel. La première version passait `--home` AVANT la sous-commande — argparse le porte sur la
    SOUS-commande, donc la MAJ mourait à l'étape de l'instantané. Aucune relecture ne l'avait vu ; seul un
    appel réel le montre, et c'est le genre de bug qu'un `monkeypatch` de `subprocess.run` cache pour de
    bon."""
    console = Path(sys.executable).with_name("forgemaster")
    if not console.is_file():
        pytest.skip(f"pas de commande `forgemaster` à côté de {sys.executable} — rien à mettre en contact")
    dest = apply_update.take_snapshot(console, live.home, lambda _m: None)
    assert (dest / snapshot.MANIFEST).is_file()
    assert dest.parent == live.home / "snapshots"


def test_un_ancien_forgemaster_qui_ignore_le_verbe_snapshot_fait_echouer_la_MAJ(
        live: Settings, tmp_path: Path):
    """On ne bascule jamais sur une version dont on ne saurait pas revenir : si la prise échoue, la MAJ
    s'arrête là, et le message dit avec QUEL binaire elle a échoué."""
    vieux = tmp_path / "vieux-forgemaster"
    vieux.write_text("#!/bin/sh\necho 'usage: forgemaster' >&2\nexit 2\n", encoding="utf-8")
    vieux.chmod(0o755)
    with pytest.raises(apply_update.UpdateFailed, match="MAJ annulée"):
        apply_update.take_snapshot(vieux, live.home, lambda _m: None)


# --- le script autonome ------------------------------------------------------------------------------

def test_apply_ne_depend_de_rien_du_forgemaster():
    """Même exigence que `restore.py`, pour la même raison : ce script tourne pendant qu'on remplace le venv
    du forgemaster, sous le `python3` du système. Vérifié par AST — une régression d'import ne se voit pas à
    la
    relecture six mois plus tard."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(Path(apply_update.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "import relatif : le script ne tournerait plus hors du paquet"
            modules.add((node.module or "").split(".")[0])
    assert "forgemaster" not in modules
    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)


def _est_popen(func: ast.expr) -> bool:
    return (isinstance(func, ast.Attribute) and func.attr == "Popen") or (
        isinstance(func, ast.Name) and func.id == "Popen")


def test_aucun_popen_de_maj_ne_laisse_sa_sortie_a_son_lanceur():
    """MESURÉ le 2026-08-06, à travers le rebond ssh réel : un descendant qui garde ouverte la sortie du
    canal retient ce canal EXACTEMENT le temps de sa vie — 25,2 s contre 0,4 s quand cette sortie va dans un
    fichier. Ce n'est PAS l'entrée standard qui compte : poser `stdin=DEVNULL` a été mesuré sans effet, et sa
    prémisse (un esclave de pty tenu par le petit-fils) est absente de ce chemin. C'est la SORTIE.

    Les deux `Popen` du chemin de MAJ redirigent déjà — ce test ne corrige rien, il FIGE. Et il couvre le
    troisième que quelqu'un ajoutera : par AST, parce qu'un `Popen` nu ne se voit pas à la relecture, et que
    le symptôme qu'il produirait (« la commande ne rend pas la main ») ne ressemble pas à sa cause."""
    for module in (update, apply_update):
        nom = Path(module.__file__).name
        for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and _est_popen(node.func)):
                continue
            passes = {k.arg for k in node.keywords}
            for flux in ("stdout", "stderr"):
                assert flux in passes, (
                    f"{nom}:{node.lineno} — `Popen` sans `{flux}=` : le descendant hérite ce flux de son "
                    f"lanceur, et à travers un rebond ssh il retient le canal jusqu'à sa mort")


def test_le_verbe_copie_le_script_au_lieu_de_le_lancer_depuis_le_paquet(live: Settings, tmp_path: Path,
                                                                       monkeypatch):
    """Le script doit survivre au venv qu'il remplace : on le copie dans le dossier de run avant de le
    lancer. Le lancer depuis `site-packages` reviendrait à scier la branche pendant qu'on est dessus."""
    lances = _capture_lancements(monkeypatch)
    plan = {"wheel": tmp_path / "c.whl", "home": live.home, "link": live.home / "current",
            "base_url": "http://127.0.0.1:8700", "scope": "user",
            "unit": tmp_path / "u", "venv": Path("/x")}
    monkeypatch.setattr(update, "follow", lambda run_dir, **_k: 0)
    assert update.launch(live, plan, systemctl="systemctl", service="forgemaster", detach=False) == 0

    # Le script se cherche DANS l'argv, jamais à un index : depuis le 2026-08-06 la commande commence par
    # `systemd-run` et ses propriétés, et un test ancré sur `cmd[1]` casserait sur un préfixe qui grandit
    # alors que le contrat qu'il garde — le script est COPIÉ, pas lancé depuis le paquet — n'a pas bougé.
    script = Path(next(a for a in lances[0] if a.endswith(update.APPLY)))
    assert script.name == "apply.py" and script.parent.parent == live.home / "updates"
    assert script.read_text(encoding="utf-8") == Path(apply_update.__file__).read_text(encoding="utf-8")
    assert os.access(script, os.X_OK)


# --- l'échappement au cgroup (2026-08-06) --------------------------------------------------------------
#
# Mesuré sur vrai systemd : `Popen(start_new_session=True)` change la SESSION, pas le CGROUP. Lancé par le
# daemon, l'applicateur mourait du `systemctl stop` qu'il émet lui-même, sans écrire de verdict. Ces tests
# gardent la forme du remède, pas son détail : ce qui compte est qu'il y ait une unité transitoire, qu'elle
# soit de la BONNE portée, et qu'un enregistrement refusé se dise.

def _plan_de_lancement(live: Settings, tmp_path: Path, scope: str) -> dict:
    return {"wheel": tmp_path / "c.whl", "home": live.home, "link": live.home / "current",
            "base_url": "http://127.0.0.1:8700", "scope": scope,
            "unit": tmp_path / "u", "venv": Path("/x")}


@pytest.mark.parametrize(("scope", "attendu"), [("user", True), ("system", False)])
def test_lapplicateur_part_dans_sa_PROPRE_unite_transitoire(live: Settings, tmp_path: Path, monkeypatch,
                                                            scope: str, attendu: bool):
    """Il doit survivre au service qu'il arrête. `--user` suit la PORTÉE : une unité transitoire de portée
    système ne serait pas pilotable par un gestionnaire `user`, et l'inverse ne pourrait pas toucher au
    service système."""
    lances = _capture_lancements(monkeypatch)
    monkeypatch.setattr(update, "follow", lambda run_dir, **_k: 0)
    update.launch(live, _plan_de_lancement(live, tmp_path, scope),
                  systemctl="systemctl", service="forgemaster", detach=True)

    cmd = lances[0]
    assert Path(cmd[0]).name == update.RUNNER, f"l'applicateur n'est pas lancé par {update.RUNNER} : {cmd}"
    assert ("--user" in cmd) is attendu, f"portée {scope} : `--user` mal placé dans {cmd}"
    assert "--collect" in cmd, "une unité qui reste en `failed` occuperait son nom au run suivant"


def test_lunite_transitoire_DERIVE_du_dossier_de_run(live: Settings, tmp_path: Path, monkeypatch):
    """Le run et son unité ne peuvent pas diverger — donc depuis un dossier de run on sait quoi interroger,
    sans mémoire externe. C'est ce que la route de la sous-phase suivante lira."""
    lances = _capture_lancements(monkeypatch)
    monkeypatch.setattr(update, "follow", lambda run_dir, **_k: 0)
    update.launch(live, _plan_de_lancement(live, tmp_path, "user"),
                  systemctl="systemctl", service="forgemaster", detach=True)

    cmd = lances[0]
    run_dir = Path(next(a for a in cmd if a.endswith(update.APPLY))).parent
    assert f"--unit=forgemaster-update-{run_dir.name}" in cmd, f"unité non dérivée du run : {cmd}"


def test_le_journal_precoce_survit_a_lechappement(live: Settings, tmp_path: Path, monkeypatch):
    """`launch.log` porte ce qui arrive AVANT que l'applicateur ouvre son propre `journal.log`. Une unité
    transitoire n'hérite d'aucun descripteur : sans `append:`, cette trace-là disparaîtrait en silence."""
    lances = _capture_lancements(monkeypatch)
    monkeypatch.setattr(update, "follow", lambda run_dir, **_k: 0)
    update.launch(live, _plan_de_lancement(live, tmp_path, "user"),
                  systemctl="systemctl", service="forgemaster", detach=True)

    cmd = lances[0]
    run_dir = Path(next(a for a in cmd if a.endswith(update.APPLY))).parent
    for flux in ("StandardOutput", "StandardError"):
        assert f"{flux}=append:{run_dir / 'launch.log'}" in cmd, f"{flux} n'atterrit pas dans launch.log"


def test_un_enregistrement_REFUSE_se_dit_au_lieu_de_se_taire(live: Settings, tmp_path: Path, monkeypatch,
                                                             capsys):
    """Avec le fire-and-forget d'avant, un applicateur qui ne partait pas passait pour un applicateur lent :
    `follow` attendait le quart d'heure entier puis rendait « je ne sais pas ». Le système, lui, avait déjà
    répondu « non »."""
    _capture_lancements(monkeypatch, rc=1, err="Failed to start transient service unit: Access denied")
    ecoule: list[bool] = []
    monkeypatch.setattr(update, "follow", lambda run_dir, **_k: ecoule.append(True) or 0)

    rc = update.launch(live, _plan_de_lancement(live, tmp_path, "user"),
                       systemctl="systemctl", service="forgemaster", detach=False)

    assert rc != 0 and not ecoule, "on a suivi un journal que personne n'écrira"
    assert "Access denied" in capsys.readouterr().err, "le motif du système est perdu en route"


def test_un_lancement_qui_nA_PAS_LIEU_rend_un_rc_au_lieu_dune_trace(live: Settings, tmp_path: Path,
                                                                    monkeypatch, capsys):
    """`launch` est appelé HORS du `try` de `cli_dispatch` : une `UpdateRefused` qui en sortirait deviendrait
    une trace nue. Les deux façons de ne pas partir — le lanceur disparu entre le préflight et ici, et un
    gestionnaire systemd qui ne répond pas — rendent donc un rc, comme le refus d'enregistrement."""
    monkeypatch.setattr(update, "follow", lambda run_dir, **_k: 0)
    plan = _plan_de_lancement(live, tmp_path, "user")

    # `which` est piloté dans les DEUX volets, jamais laissé à l'hôte : un test dont le résultat dépend de
    # la présence de `systemd-run` sur la machine de build mesure la machine, pas le code.
    vrai = update.shutil.which
    absent = lambda nom, *a, **k: None if nom == update.RUNNER else vrai(nom, *a, **k)          # noqa: E731
    present = lambda nom, *a, **k: "/usr/bin/systemd-run" if nom == update.RUNNER else vrai(nom)  # noqa: E731

    monkeypatch.setattr(update.shutil, "which", absent)
    assert update.launch(live, plan, systemctl="systemctl", service="forgemaster", detach=False) == 2

    def _bloque(cmd, **_kw):
        raise subprocess.TimeoutExpired(cmd, update.REGISTER_TIMEOUT)

    monkeypatch.setattr(update.shutil, "which", present)
    monkeypatch.setattr(update.subprocess, "run", _bloque)
    assert update.launch(live, plan, systemctl="systemctl", service="forgemaster", detach=False) == 2
    assert "gestionnaire systemd" in capsys.readouterr().err


def test_sans_systemd_run_le_preflight_REFUSE_avant_tout_effet(live: Settings, tmp_path: Path, monkeypatch):
    """Le refus est au préflight, pas au lancement : à ce moment-là « rien n'a été touché » est exactement
    vrai, et `--dry-run` le dit aussi. Sans échappement, l'applicateur se ferait tuer par l'arrêt qu'il
    émet — le poser quand même serait poser un geste qu'on sait cassé."""
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    vrai = update.shutil.which
    monkeypatch.setattr(update.shutil, "which",
                        lambda nom, *a, **k: None if nom == update.RUNNER else vrai(nom, *a, **k))
    with pytest.raises(UpdateRefused, match=update.RUNNER):
        update.preflight(live, wheel=str(whl), unit=None, scope="user")


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
    assert f"ExecStart={link}/bin/forgemaster serve" in unit.read_text(encoding="utf-8")


def test_le_venv_et_le_lien_sont_dits_hors_perimetre_de_linstantane(live: Settings):
    """Un instantané couvre la DONNÉE, pas le binaire — c'est pour ça que le retour arrière demande deux
    gestes. L'exclusion voyage dans le manifeste pour que la frontière soit lisible dans l'artefact."""
    dest = snapshot.create(live)
    exclus = json.loads((dest / snapshot.MANIFEST).read_text(encoding="utf-8"))["excluded"]
    assert {"venvs/", "current", "updates/"} <= set(exclus)


def _capture_lancements(monkeypatch, *, rc: int = 0, err: str = "") -> list[list[str]]:
    """Intercepte le lancement de l'applicateur et rend les argv vus. Cible `subprocess.run` (et non plus
    `Popen`) depuis le 2026-08-06 : le lanceur LIT désormais le verdict d'enregistrement de `systemd-run`,
    ce qu'un fire-and-forget ne permettait pas."""
    lances: list[list[str]] = []

    def _run(cmd, **_kw):
        lances.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=err)

    monkeypatch.setattr(update.subprocess, "run", _run)
    return lances


def _slugs(db: Path) -> list[str]:
    conn = sqlite3.connect(str(db))
    try:
        return sorted(r[0] for r in conn.execute("SELECT slug FROM projects"))
    finally:
        conn.close()


# --- le garde de compatibilité vu du retour arrière AUTOMATIQUE ---------------------------------------
#
# Le garde de `restore.py` refuse une remise dont il ne peut pas vérifier le binaire en place. Le retour
# arrière automatique, lui, a une connaissance que le garde n'a pas : le lien vient d'être rebasculé sur le
# venv qui a PRIS cet instantané. Il lève donc le DOUTE (indéterminable), jamais la CERTITUDE (incompatible).

def test_le_retour_arriere_automatique_nest_pas_bloque_par_un_binaire_indeterminable(
        live: Settings, tmp_path: Path, monkeypatch):
    """Régression : sans la levée du doute, ce retour arrière échouerait sur un venv que `restore.py` ne sait
    pas interroger — l'utilisateur se retrouverait avec le binaire d'avant et les données d'après, soit
    exactement l'état que l'invariant interdit."""
    assert not (live.home / "current" / "bin" / "python").exists(), (
        "prémisse ratée : ce venv de décor doit être NON interrogeable")
    shim, _ = _systemctl_shim(tmp_path)
    neuf = tmp_path / "venvs" / "neuf"
    (neuf / "bin").mkdir(parents=True)
    dest = snapshot.create(live)
    monkeypatch.setattr(apply_update, "build_blue", lambda *a, **k: neuf / "bin" / "forgemaster")
    monkeypatch.setattr(apply_update, "probe_isolated", lambda *a, **k: {"version": "9.9", "sha": "beef"})
    monkeypatch.setattr(apply_update, "take_snapshot", lambda *a, **k: dest)
    monkeypatch.setattr(apply_update, "_verify_live", lambda *a, **k: (False, "il ne sert pas"))
    monkeypatch.setattr(apply_update, "_wait_health", lambda *a, **k: (True, ""))

    rc, verdict, _ = apply_update.apply(_args(live, tmp_path, shim), lambda _m: None)

    assert rc == 1 and "revenue à l'état d'avant" in verdict
    assert _slugs(live.db_path) == ["atelier-fictif"]


def test_le_drapeau_nest_pas_passe_a_un_restore_qui_ne_le_connait_pas(tmp_path: Path):
    """Le piège d'argparse : un instantané pris AVANT ce garde embarque un `restore.py` qui sortirait en usage
    sur un drapeau inconnu — et ferait échouer le retour arrière au pire moment. On lit donc le script figé
    avant de lui parler."""
    ancien = tmp_path / "ancien.py"
    ancien.write_text("import argparse\n# ne connaît que --snapshot et --home\n", encoding="utf-8")
    moderne = tmp_path / "moderne.py"
    moderne.write_text(f"p.add_argument({apply_update.FORCE_FLAG!r})\n", encoding="utf-8")

    assert apply_update._supports_flag(ancien, apply_update.FORCE_FLAG) is False
    assert apply_update._supports_flag(moderne, apply_update.FORCE_FLAG) is True
    assert apply_update._supports_flag(tmp_path / "jamais.py", apply_update.FORCE_FLAG) is False


def test_le_restore_reellement_pose_porte_le_drapeau(live: Settings):
    """Le lien entre les deux tests précédents : la copie que le produit fige AUJOURD'HUI dans chaque
    instantané connaît le drapeau. Sans cette ligne, `_supports_flag` renverrait toujours faux et le retour
    arrière automatique retomberait dans le refus — vert en apparence, cassé en vrai."""
    dest = snapshot.create(live)
    assert apply_update._supports_flag(dest / snapshot.RESTORE, apply_update.FORCE_FLAG) is True


# --- la rétention des venvs -------------------------------------------------------------------------
#
# Rien ne gardait cette purge, et c'est le seul geste IRRÉVERSIBLE d'une MAJ réussie. L'ancienne
# formulation — « garder les KEEP_VENVS plus récents » — donnait le bon résultat à ROLLBACK_DEPTH = 1, et
# seulement là : `keep` remplissait tout le quota, la date n'avait aucune place où s'exprimer. Ces tests
# figent la formulation qui reste vraie quand la profondeur change, pas la coïncidence.

def _venvs_factices(root: Path, *noms: str) -> list[Path]:
    for nom in noms:
        (root / nom).mkdir(parents=True)
    return [root / nom for nom in noms]


def test_la_purge_ne_garde_QUE_les_venvs_joignables_par_un_retour_arriere(tmp_path: Path):
    """L'appelant vient de faire la bascule : il SAIT lesquels sont joignables. On ne les re-devine pas."""
    root = tmp_path / "venvs"
    vieux, cible, courant = _venvs_factices(root, "2026-01-01", "2026-06-01", "2026-08-01")

    apply_update._purge_venvs(root, keep={courant, cible}, log=lambda _m: None)

    assert not vieux.exists()
    assert cible.is_dir() and courant.is_dir()


def test_un_bleu_qui_a_ECHOUE_est_purge_meme_s_il_est_le_PLUS_RECENT_des_non_gardes(tmp_path: Path):
    """Le cas que l'arithmétique par date aurait manqué dès `ROLLBACK_DEPTH = 2` : après une MAJ ratée, le
    venv le plus récent hors `keep` est un bleu qui n'a JAMAIS servi — exactement celui vers lequel il ne
    faut pas revenir. « Le plus récent » et « le cran d'avant » sont deux ordres différents."""
    root = tmp_path / "venvs"
    cible, rate, courant = _venvs_factices(root, "2026-06-01", "2026-07-01", "2026-08-01")

    apply_update._purge_venvs(root, keep={courant, cible}, log=lambda _m: None)

    assert not rate.exists(), "un bleu jamais servi a survécu à la purge parce qu'il était récent"
    assert cible.is_dir()


def test_une_declaration_incoherente_avec_la_politique_ne_purge_RIEN(tmp_path: Path):
    """Fail-closed sur le seul geste irréversible de la MAJ : si la liste des joignables ne correspond pas
    à la politique, on ne devine pas ce qu'il faut supprimer — on ne supprime pas, et on le dit."""
    root = tmp_path / "venvs"
    a, b, c = _venvs_factices(root, "2026-01-01", "2026-06-01", "2026-08-01")
    dits: list[str] = []

    apply_update._purge_venvs(root, keep={c}, log=dits.append)      # 1 déclaré, la politique en attend 2

    assert all(p.is_dir() for p in (a, b, c))
    assert any("purge sautée" in ligne for ligne in dits)


def test_la_politique_est_declaree_UNE_fois_et_les_deux_retentions_en_derivent():
    """`ROLLBACK_DEPTH` vit chez le module stdlib-pur parce que `snapshot` peut le lire, jamais l'inverse."""
    assert apply_update.KEEP_VENVS == apply_update.ROLLBACK_DEPTH + 1
    assert snapshot.KEEP == apply_update.ROLLBACK_DEPTH + 2


# --- la route lanceuse : un état qui SURVIT au daemon (2026-08-06, sous-phase 3a·2b) -------------------
#
# Le processus qui répond au `GET` d'après n'est ni celui qui a reçu le `POST`, ni même le même binaire : la
# bascule est passée entre les deux. Tout ce qui suit garde donc UNE propriété — l'état d'un run se relit du
# disque, et il sait dire « je ne sais pas ».

def _run_sur_disque(live: Settings, nom: str = "2026-08-06T10-00-00Z", **fichiers: str) -> Path:
    """Un dossier de run construit à la main : on juge la LECTURE d'état, pas le lanceur qui l'a produit."""
    run_dir = live.home / update.UPDATES / nom
    run_dir.mkdir(parents=True)
    for base, contenu in fichiers.items():
        (run_dir / base).write_text(contenu, encoding="utf-8")
    return run_dir


def test_spawn_ecrit_lINTENTION_avant_de_savoir_si_le_lancement_partira(live: Settings, tmp_path: Path,
                                                                       monkeypatch, capsys):
    """Le mode ne se dérive de RIEN : `result.json` ne le porte pas et n'existe qu'à la fin. Un run qui n'a
    jamais démarré doit quand même dire ce qu'il allait faire — sinon la liste des runs affiche des dossiers
    muets. Et le cœur ne PARLE pas : appelé depuis une requête HTTP, un `print` finirait dans le journal du
    service."""
    _capture_lancements(monkeypatch, rc=1, err="Access denied")
    issue = update.spawn(live, _plan_de_lancement(live, tmp_path, "user"),
                         systemctl="systemctl", service="forgemaster")

    assert issue["ok"] is False and "Access denied" in issue["detail"]
    assert capsys.readouterr() == ("", ""), "le cœur a parlé — la route l'appelle depuis une requête"
    meta = json.loads((issue["run"] / update.RUN_META).read_text(encoding="utf-8"))
    assert meta["mode"] == "apply" and meta["scope"] == "user"
    assert meta["unit"] == issue["unit"] == f"forgemaster-update-{issue['run'].name}"
    assert meta["wheel"].endswith("c.whl"), "l'intention ne dit pas ce qu'elle allait poser"


def test_un_run_TERMINE_rend_son_verdict_sans_interroger_personne(live: Settings):
    """Le cas nominal d'après la bascule : `result.json` est là, et il tranche AVANT toute question à
    systemd — l'unité a été effacée par `--collect`, l'interroger ne dirait rien d'utile."""
    run_dir = _run_sur_disque(
        live, **{update.RUN_META: '{"mode": "apply", "scope": "user"}',
                 update.RUN_RESULT: '{"rc": 0, "verdict": "MAJ posée"}',
                 update.RUN_JOURNAL: "== MAJ lancée\n"})
    demandes: list[tuple[str, str]] = []

    etat = update.run_state(run_dir, is_active=lambda u, s: demandes.append((u, s)) or True)

    assert etat["state"] == "done" and etat["rc"] == 0 and etat["verdict"] == "MAJ posée"
    assert etat["mode"] == "apply" and etat["journal"].startswith("== MAJ lancée")
    assert demandes == [], "l'unité a été interrogée alors que le verdict était déjà écrit"


def test_un_run_qui_a_ECHOUE_est_distinct_dun_run_qui_a_REUSSI(live: Settings):
    """Un rc non nul n'est pas une absence de verdict : c'est un verdict, et il porte son motif."""
    run_dir = _run_sur_disque(live, **{update.RUN_RESULT: '{"rc": 3, "verdict": "le vivant ne sert pas"}'})

    etat = update.run_state(run_dir)

    assert etat["state"] == "failed" and etat["rc"] == 3 and "ne sert pas" in etat["verdict"]


def test_sans_sonde_un_run_SANS_verdict_avoue_au_lieu_de_conclure(live: Settings):
    """Le garde-fou de l'économie de sonde. Sans avoir demandé au gestionnaire, on ne peut PAS conclure à
    `interrupted` : ce serait annoncer une mort qu'on n'a pas vérifiée — et c'est exactement ce qu'un appelant
    qui saute la sonde (la liste) produirait sinon."""
    run_dir = _run_sur_disque(live, **{update.RUN_JOURNAL: "== MAJ lancée\n"})

    etat = update.run_state(run_dir)                      # aucune sonde injectée

    assert etat["state"] == "unknown" and "pas été sondée" in etat["verdict"]


def test_un_run_SANS_verdict_dont_lunite_TOURNE_est_en_cours(live: Settings):
    """L'unité se dérive du dossier de run : on sait quoi interroger sans aucune mémoire externe. C'est la
    seule chose que la sonde systemd sert à trancher — `running` contre `interrupted`."""
    run_dir = _run_sur_disque(live, **{update.RUN_META: '{"mode": "apply", "scope": "user"}',
                                       update.RUN_JOURNAL: "== MAJ lancée\n"})
    demandes: list[tuple[str, str]] = []

    etat = update.run_state(run_dir, is_active=lambda u, s: demandes.append((u, s)) or True)

    assert etat["state"] == "running" and etat["rc"] is None
    assert demandes == [(f"forgemaster-update-{run_dir.name}", "user")]


def test_un_run_PARTI_sans_verdict_et_sans_unite_dit_QUIL_NE_SAIT_PAS(live: Settings):
    """L'état que le fire-and-forget d'avant ne savait pas dire : il ne restait qu'un silence, impossible à
    distinguer d'une attente. Une lecture d'état qui ne sait pas dire « je ne sais pas » n'en est pas une."""
    run_dir = _run_sur_disque(live, **{update.RUN_LAUNCH: "Running as unit …\n"})

    etat = update.run_state(run_dir, is_active=lambda _u, _s: False)

    assert etat["state"] == "interrupted" and etat["rc"] is None and etat["verdict"]


def test_un_run_qui_nA_JAMAIS_demarre_ne_se_confond_pas_avec_un_run_interrompu(live: Settings):
    """`launch.log` est ouvert par l'UNITÉ, à son démarrage. Son absence CONJOINTE avec celle du verdict est
    donc ce qui sépare « enregistré, jamais parti » de « parti, jamais conclu » — et seul le premier permet
    de dire « rien n'a bougé sur l'instance »."""
    run_dir = _run_sur_disque(live, **{update.RUN_META: '{"mode": "rollback", "scope": "user"}'})

    etat = update.run_state(run_dir, is_active=lambda _u, _s: False)

    assert etat["state"] == "never_started" and "rien n'a bougé" in etat["verdict"]
    assert etat["mode"] == "rollback"


def test_la_liste_des_runs_DIT_sa_borne_au_lieu_de_la_taire(live: Settings):
    """Invariant de la forge : jamais de cap silencieux. Une liste tronquée sans le dire se lit « c'est
    tout » — et sur une instance qui accumule ses runs, ce serait faux dès le 51ᵉ."""
    for i in range(4):
        _run_sur_disque(live, f"2026-08-0{i + 1}T10-00-00Z", **{update.RUN_RESULT: '{"rc": 0}'})
    (live.home / update.UPDATES / "pas-un-run").mkdir()      # un intrus ne devient pas un run

    vue = update.list_runs(live, limit=2)

    assert [r["run"] for r in vue["runs"]] == ["2026-08-04T10-00-00Z", "2026-08-03T10-00-00Z"]
    assert vue["total"] == 4 and vue["truncated"] is True
    assert all(r["journal"] == "" for r in vue["runs"]), "la liste porte des journaux qu'elle n'affiche pas"


def test_la_liste_ne_depense_QU_UNE_sonde_systemd(live: Settings):
    """Sonder chaque run coûterait jusqu'à `limit` allers-retours au gestionnaire sur une simple vue de
    liste, et le plafond tomberait sur une requête HTTP le jour où ce gestionnaire est coincé — le jour où
    l'on regarde justement cette page. La sonde va au run sans verdict le plus RÉCENT ; les suivants
    rendent `unknown`, jamais un `interrupted` que personne n'a vérifié."""
    _run_sur_disque(live, "2026-08-01T10-00-00Z", **{update.RUN_JOURNAL: "vieux\n"})
    _run_sur_disque(live, "2026-08-02T10-00-00Z", **{update.RUN_RESULT: '{"rc": 0}'})
    _run_sur_disque(live, "2026-08-03T10-00-00Z", **{update.RUN_JOURNAL: "récent\n"})
    demandes: list[str] = []

    vue = update.list_runs(live, is_active=lambda u, _s: demandes.append(u) or True)

    assert [(r["run"], r["state"]) for r in vue["runs"]] == [
        ("2026-08-03T10-00-00Z", "running"),
        ("2026-08-02T10-00-00Z", "done"),
        ("2026-08-01T10-00-00Z", "unknown")]
    assert demandes == ["forgemaster-update-2026-08-03T10-00-00Z"], f"sondes dépensées : {demandes}"


def test_un_identifiant_de_run_venu_du_reseau_nEST_PAS_un_chemin(live: Settings):
    """Deux gardes, et aucune ne suffit seule : la FORME (`..`, un chemin absolu), puis le CONFINEMENT du
    chemin résolu — une forme valide peut être un lien symbolique posé là par autre chose."""
    _run_sur_disque(live)
    dehors = live.home / "secret"
    dehors.mkdir()
    os.symlink(dehors, live.home / update.UPDATES / "2026-01-01T00-00-00Z")

    assert update.run_dir_for(live, "2026-08-06T10-00-00Z").is_dir()
    for hostile in ("../../etc", "..", ".", "/etc", "2026-08-06T10-00-00Z/../..", "inconnu",
                    "2026-01-01T00-00-00Z"):
        with pytest.raises(KeyError):
            update.run_dir_for(live, hostile)


# --- le sixième refus : ne pas emporter un travail en cours --------------------------------------------

def test_un_dispatch_en_cours_BLOQUE_les_deux_gestes_et_les_nomme(live: Settings, tmp_path: Path,
                                                                  monkeypatch):
    """L'arrêt du service tue le worker in-process ; le boot suivant le réape `killed`, sa task retombe
    `todo`, et les jetons déjà dépensés sont perdus. Le consentement porte sur *appliquer la MAJ*, jamais
    sur *perdre le travail en cours*. Le refus vit dans le préflight PARTAGÉ — la CLI arrête exactement le
    même service, elle doit en hériter."""
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    _unit(tmp_path / "u", str(live.home / "current" / "bin" / "forgemaster"))
    en_vol = [{"job_id": "j-1", "project": "atelier-fictif", "feature": "f", "task": "t",
               "started_at": "2026-08-06T09:00:00Z"}]

    for geste in ("apply", "rollback"):
        with pytest.raises(UpdateRefused) as exc:
            if geste == "apply":
                update.preflight(live, wheel=str(whl), unit=str(tmp_path / "u"), scope="user",
                                 in_flight=en_vol)
            else:
                update.preflight_rollback(live, snapshot=None, unit=str(tmp_path / "u"), scope="user",
                                          in_flight=en_vol)
        assert "atelier-fictif/f/t" in str(exc.value) and "j-1" in str(exc.value)
        assert "abort" in str(exc.value), "le refus ne dit pas comment le lever"


def test_une_base_absente_ou_illisible_ne_BLOQUE_pas_la_mise_a_jour(tmp_path: Path):
    """Dégradation honnête, même contrat que `survey_authority` : « je ne sais pas » n'est pas « non ». Un
    refus qui se déclencherait sur une base illisible interdirait la MAJ des instances qu'il faut justement
    mettre à jour — celles dont la base est en retard."""
    vierge = Settings.resolve(home=tmp_path / "vide", projects_root=tmp_path / "p")
    assert update.survey_in_flight(vierge) == []

    vierge.home.mkdir(parents=True, exist_ok=True)
    vierge.db_path.write_bytes(b"ceci n'est pas une base sqlite")
    assert update.survey_in_flight(vierge) == []


def test_survey_in_flight_voit_un_VRAI_job_running(live: Settings):
    """La sonde lit la même table que l'abort, par le même join — mais à l'échelle de l'INSTANCE : une MAJ
    n'arrête pas un projet, elle arrête le daemon."""
    from forgemaster.dispatch import jobs
    from forgemaster.roadmap import model

    conn = store.open_db(live)
    model.add_feature(conn, project_slug="atelier-fictif", slug="f-fictive")
    tache = model.add_task(conn, feature_ref="atelier-fictif/f-fictive", slug="t-fictive")
    jid = jobs.record_start(conn, task_id=tache["id"], worktree="/tmp/wt", session_id="s-fictive")
    conn.close()

    en_vol = update.survey_in_flight(live)

    assert [(j["job_id"], j["project"], j["feature"], j["task"]) for j in en_vol] == [
        (jid, "atelier-fictif", "f-fictive", "t-fictive")]


def test_les_shells_du_terminal_web_sont_DITS_et_ne_bloquent_pas(live: Settings, tmp_path: Path):
    """Ils meurent aussi (ils vivent dans le cgroup du daemon), mais un onglet ouvert n'est pas du travail
    en cours : bloquer dessus rendrait la MAJ impossible à qui laisse un shell ouvert, c'est-à-dire à tout
    le monde. Ce qui bloque, c'est ce qui COÛTE — un dispatch."""
    whl = tmp_path / "c.whl"
    whl.write_bytes(b"")
    _unit(tmp_path / "u", str(live.home / "current" / "bin" / "forgemaster"))

    plan = update.preflight(live, wheel=str(whl), unit=str(tmp_path / "u"), scope="user",
                            sessions=["atelier-fictif", "interview:autre"])

    dit = "\n".join(update.describe(plan))
    assert "2 shell(s)" in dit and "atelier-fictif" in dit
