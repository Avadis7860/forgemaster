"""Tests de `routes/update` — la surface HTTP du cycle de MAJ, et la propriété qui la rend tenable.

**La propriété** : l'état d'un run se relit **du disque**. Le processus qui répond au `GET` d'après n'est ni
celui qui a reçu le `POST`, ni même le même binaire — la bascule est passée entre les deux. Un registre en
mémoire rendrait « inconnu » exactement au moment où l'utilisateur attend son verdict.

Ces tests la gardent **en process** (une seconde app construite sur le même disque) ; la preuve en vivant se
prend sur vrai systemd, VM 9311, par l'acte `route` du banc — là où le binaire change VRAIMENT.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgemaster import update
from forgemaster.config import Settings
from forgemaster.daemon import app as app_mod
from forgemaster.db import store

W = "forgemaster-0.1.0-py3-none-any.whl"


@pytest.fixture
def instance(tmp_path: Path, monkeypatch):
    """Une instance installée, vue par le réseau. `HOME` est détourné parce que la route ne prend **pas**
    d'`--unit` : l'unité est celle de la portée, donc `service.unit_path` doit trouver la nôtre et pas celle
    de la machine qui fait tourner les tests. `systemd-run` est piloté dans les deux volets pour la même
    raison — un test dont le résultat dépend de l'hôte mesure l'hôte."""
    faux_home = tmp_path / "faux-home"
    (faux_home / ".config/systemd/user").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(faux_home))
    vrai = update.shutil.which
    monkeypatch.setattr(update.shutil, "which",
                        lambda nom, *a, **k: "/usr/bin/systemd-run" if nom == update.RUNNER
                        else vrai(nom, *a, **k))

    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    store.open_db(settings).close()
    venv = tmp_path / "venvs" / "avant"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "forgemaster").write_text("#!/bin/sh\nexit 0\n")
    os.symlink(venv, settings.home / "current")
    exec_bin = settings.home / "current" / "bin" / "forgemaster"
    (faux_home / ".config/systemd/user/forgemaster.service").write_text(
        f"[Service]\nExecStart={exec_bin} serve --host 127.0.0.1 --port 8700\n", encoding="utf-8")

    wheel = tmp_path / W
    wheel.write_bytes(b"PK\x03\x04")
    return TestClient(app_mod.build_app(settings)), settings, wheel


def _pas_de_vrai_systemd(monkeypatch, *, rc: int = 0, err: str = "") -> list[list[str]]:
    """Intercepte l'enregistrement de l'unité — on juge la ROUTE, pas le gestionnaire systemd."""
    lances: list[list[str]] = []

    def _run(cmd, **_kw):
        lances.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=err)

    monkeypatch.setattr(update.subprocess, "run", _run)
    return lances


# --- la prévisualisation est un GET, pas un drapeau de POST --------------------------------------------

def test_le_plan_est_IDEMPOTENT_et_ne_cree_aucun_run(instance, monkeypatch):
    """La prévisualisation d'un geste mutant est un `GET` idempotent — même doctrine que
    `GET .../git/sync` avant `POST .../git/sync/reconcile`. Un `dry_run` dans le corps d'un `POST`
    obligerait à faire confiance à un drapeau pour ne rien casser. Mesuré, pas déclaré : deux appels, et le
    dossier des runs n'existe toujours pas."""
    client, settings, wheel = instance
    lances = _pas_de_vrai_systemd(monkeypatch)

    for _ in range(2):
        r = client.get("/api/update/plan", params={"mode": "apply", "wheel": str(wheel)})
        assert r.status_code == 200, r.text
        corps = r.json()
        assert any("wheel à poser" in ligne for ligne in corps["describe"])
        assert corps["plan"]["base_url"] == "http://127.0.0.1:8700"

    assert lances == [], "un GET de prévisualisation a lancé quelque chose"
    assert not (settings.home / update.UPDATES).exists(), "le plan a créé un dossier de run"


def test_un_refus_du_preflight_arrive_en_409_avec_son_TEXTE(instance):
    """Jamais un « impossible » nu : les six refus voyagent entiers, avec le geste qui les lève. C'est ce
    qui fait la différence entre une surface qu'on peut utiliser seul et une qui renvoie au terminal."""
    client, _settings, _wheel = instance

    r = client.get("/api/update/plan", params={"mode": "apply", "wheel": "/nulle/part/x.whl"})

    assert r.status_code == 409
    assert "wheel introuvable" in r.json()["detail"]


def test_un_wheel_manquant_est_un_400_pas_un_500(instance):
    """`mode=apply` sans `wheel` est une requête malformée, pas un état de l'instance : elle ne se déguise
    pas en refus (409) ni en panne (500)."""
    client, _settings, _wheel = instance

    assert client.get("/api/update/plan", params={"mode": "apply"}).status_code == 400


# --- le POST lance, et rend de quoi retrouver le verdict -----------------------------------------------

def test_le_POST_rend_202_et_lidentifiant_du_run(instance, monkeypatch):
    """202, pas 201 : c'est accepté et parti, pas fini — le daemon va mourir puis revenir. La réponse porte
    l'identifiant du run, seule chose dont l'appelant a besoin de l'autre côté de la bascule."""
    client, settings, wheel = instance
    lances = _pas_de_vrai_systemd(monkeypatch)

    r = client.post("/api/update/apply", json={"wheel": str(wheel)})

    assert r.status_code == 202, r.text
    corps = r.json()
    assert corps["mode"] == "apply" and corps["state"] == "running"
    assert corps["unit"] == f"forgemaster-update-{corps['run']}"
    assert Path(lances[0][0]).name == update.RUNNER, (
        f"l'applicateur n'est pas parti dans son unité : {lances[0]}")
    assert (settings.home / update.UPDATES / corps["run"] / update.RUN_META).is_file()


def test_LETAT_dun_run_se_relit_apres_la_MORT_du_daemon(instance, monkeypatch):
    """**La** propriété de cette surface. Le `POST` est reçu par une app, la bascule tue le daemon, et le
    `GET` est servi par une AUTRE — ici une seconde `build_app` sur le même disque, sur vrai systemd un
    binaire qui n'existait pas encore quand le `POST` est arrivé. Si l'état vivait en mémoire, ce `GET`
    rendrait 404."""
    client, settings, wheel = instance
    _pas_de_vrai_systemd(monkeypatch)
    run = client.post("/api/update/apply", json={"wheel": str(wheel)}).json()["run"]

    # L'applicateur fait son travail et écrit son verdict pendant que le daemon est à terre.
    (settings.home / update.UPDATES / run / update.RUN_RESULT).write_text(
        json.dumps({"rc": 0, "verdict": "MAJ posée, vivant vérifié"}), encoding="utf-8")

    apres = TestClient(app_mod.build_app(settings))          # nouveau processus, nouvelle mémoire
    r = apres.get(f"/api/update/runs/{run}")

    assert r.status_code == 200, r.text
    etat = r.json()
    assert etat["state"] == "done" and etat["rc"] == 0
    assert etat["verdict"] == "MAJ posée, vivant vérifié" and etat["mode"] == "apply"
    assert [x["run"] for x in apres.get("/api/update/runs").json()["runs"]] == [run]


def test_un_run_INCONNU_ou_traversant_rend_404_sans_rien_lire(instance):
    """Un identifiant qui arrive du réseau n'est pas un nom de dossier. Deux gardes (forme, puis confinement
    du chemin résolu) et le même 404 pour toutes : distinguer « mal formé » de « absent » renseignerait sur
    ce qui existe."""
    client, _settings, _wheel = instance

    # `.` et `..` NUS ne sont pas testés ici : le client HTTP normalise le chemin avant l'envoi, la route ne
    # les voit jamais (`/runs/.` arrive en `/runs`, donc en 200 sur la LISTE — mesuré, pas déduit). Ils sont
    # gardés là où ils peuvent réellement arriver : `run_dir_for`, dans `test_update.py`.
    for hostile in ("2026-08-06T10-00-00Z", "..%2F..%2Fetc", "inconnu", "2026-13-99T99-99-99Z"):
        assert client.get(f"/api/update/runs/{hostile}").status_code == 404, hostile


def test_un_enregistrement_REFUSE_rend_503_et_garde_la_trace(instance, monkeypatch):
    """Ce n'est PAS un refus de l'instance (elle était d'accord) : c'est une machinerie indisponible. Le 503
    le dit, et l'identifiant du run voyage quand même — le dossier existe, la trace est trouvable."""
    client, settings, wheel = instance
    _pas_de_vrai_systemd(monkeypatch, rc=1, err="Failed to start transient service unit: Access denied")

    r = client.post("/api/update/apply", json={"wheel": str(wheel)})

    assert r.status_code == 503
    assert "Access denied" in r.json()["detail"]
    runs = list((settings.home / update.UPDATES).iterdir())
    assert len(runs) == 1 and (runs[0] / update.RUN_META).is_file(), "la trace du run manqué a disparu"


def test_le_retour_arriere_est_expose_LUI_AUSSI(instance):
    """La surface expose l'aller ET le retour, sinon elle re-crée le lot de consolation que le retour
    volontaire a fermé. Sans instantané, elle refuse en le disant — elle n'ignore pas la route."""
    client, _settings, _wheel = instance

    r = client.post("/api/update/rollback", json={})

    assert r.status_code == 409
    assert "aucun instantané" in r.json()["detail"]


# --- ce que la route ajoute au pouvoir déjà existant de la CLI -----------------------------------------

def test_une_ORIGINE_TIERCE_ne_peut_pas_declencher_une_MAJ(instance):
    """La route la plus puissante du produit, sur un daemon qui n'authentifie aucun appelant. Ce qu'elle
    ajoute par rapport à la CLI, c'est d'être atteignable depuis un navigateur — donc la seule barrière
    mesurable est le CORS. Un corps JSON force un préflight, que l'allow-list doit refuser.

    Mesuré, pas supposé : si ce test passait au vert dans l'autre sens, une page ouverte dans un onglet
    pourrait remplacer le binaire de l'instance."""
    client, _settings, wheel = instance

    r = client.options("/api/update/apply",
                       headers={"Origin": "http://evil.example",
                                "Access-Control-Request-Method": "POST",
                                "Access-Control-Request-Headers": "content-type"})

    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}, (
        "une origine tierce est autorisée à poser un wheel sur cette instance")
    # Le contre-témoin : l'origine du dev l'est, elle. Sans lui, ce test passerait aussi sur un CORS cassé.
    ok = client.options("/api/update/apply",
                        headers={"Origin": "http://localhost:5173",
                                 "Access-Control-Request-Method": "POST",
                                 "Access-Control-Request-Headers": "content-type"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_la_surface_HTTP_est_plus_ETROITE_que_la_CLI(instance, monkeypatch):
    """Ni `unit`, ni `systemctl`, ni `service` dans le corps : ce sont des points d'injection pour un test
    ou pour un opérateur devant un terminal, pas des choses qu'on accepte du réseau. Un corps qui les porte
    ne doit pas les voir honorés."""
    client, _settings, wheel = instance
    lances = _pas_de_vrai_systemd(monkeypatch)

    r = client.post("/api/update/apply",
                    json={"wheel": str(wheel), "unit": "/tmp/pirate.service",
                          "systemctl": "/tmp/pirate", "service": "autre"})

    assert r.status_code == 202, r.text
    argv = " ".join(lances[0])
    assert "/tmp/pirate" not in argv, "un chemin venu du corps de la requête a atteint l'argv du lanceur"
    assert "--service forgemaster" in argv and "--service autre" not in argv


def test_les_shells_VIVANTS_du_terminal_sont_dits_par_la_ROUTE(instance):
    """Le câblage, pas seulement la règle : le registre vit sur `app.state`, donc seule la route peut le
    lire. Un shell déjà sorti n'est pas annoncé — annoncer une perte qui a déjà eu lieu n'informe personne."""
    client, _settings, wheel = instance

    class _Session:
        def __init__(self, vivant: bool) -> None:
            self._vivant = vivant

        def alive(self) -> bool:
            return self._vivant

    from forgemaster.terminal.registry import PtySessionRegistry
    registre = PtySessionRegistry()
    registre.put("atelier-fictif", _Session(True))        # type: ignore[arg-type]
    registre.put("deja-sorti", _Session(False))           # type: ignore[arg-type]
    client.app.state.terminals = registre

    dit = "\n".join(client.get("/api/update/plan", params={"wheel": str(wheel)}).json()["describe"])

    assert "1 shell(s)" in dit and "atelier-fictif" in dit
    assert "deja-sorti" not in dit


def test_un_DISPATCH_en_cours_bloque_la_route_en_409(instance):
    """Le sixième refus, vu du réseau. Il vit dans le préflight partagé — la route n'a rien à re-décider,
    elle apporte seulement la connaissance (la base) que le préflight ne va pas chercher tout seul."""
    from forgemaster.dispatch import jobs
    from forgemaster.projects import registry
    from forgemaster.roadmap import model

    client, settings, wheel = instance
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="atelier-fictif")
    model.add_feature(conn, project_slug="atelier-fictif", slug="f-fictive")
    tache = model.add_task(conn, feature_ref="atelier-fictif/f-fictive", slug="t-fictive")
    jobs.record_start(conn, task_id=tache["id"], worktree="/tmp/wt", session_id="s-fictive")
    conn.close()

    r = client.post("/api/update/apply", json={"wheel": str(wheel)})

    assert r.status_code == 409
    assert "atelier-fictif/f-fictive/t-fictive" in r.json()["detail"]
    assert not (settings.home / update.UPDATES).exists(), "un refus a quand même créé un dossier de run"
