"""Tests de `routes/update` — la surface HTTP du cycle de MAJ, et la propriété qui la rend tenable.

**La propriété** : l'état d'un run se relit **du disque**. Le processus qui répond au `GET` d'après n'est ni
celui qui a reçu le `POST`, ni même le même binaire — la bascule est passée entre les deux. Un registre en
mémoire rendrait « inconnu » exactement au moment où l'utilisateur attend son verdict.

Ces tests la gardent **en process** (une seconde app construite sur le même disque) ; la preuve en vivant se
prend sur vrai systemd, VM 9311, par l'acte `route` du banc — là où le binaire change VRAIMENT.
"""
from __future__ import annotations

import hashlib
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


def test_la_surface_HTTP_est_plus_ETROITE_que_la_CLI_et_le_DIT(instance, monkeypatch):
    """Ni `unit`, ni `systemctl`, ni `service` dans le corps : ce sont des points d'injection pour un test
    ou pour un opérateur devant un terminal, pas des choses qu'on accepte du réseau.

    Et le corps les **refuse** au lieu de les ignorer (`extra="forbid"`) : sur la route la plus puissante du
    produit, « ignoré » se lit « honoré » par qui l'a écrit. Un 422 dit la vérité, et aucun run ne part."""
    client, settings, wheel = instance
    lances = _pas_de_vrai_systemd(monkeypatch)

    r = client.post("/api/update/apply",
                    json={"wheel": str(wheel), "unit": "/tmp/pirate.service",
                          "systemctl": "/tmp/pirate", "service": "autre"})

    assert r.status_code == 422, r.text
    assert lances == [] and not (settings.home / update.UPDATES).exists()

    # Le contre-témoin : le corps LÉGITIME passe, et le lanceur reçoit nos valeurs, pas celles du réseau.
    ok = client.post("/api/update/apply", json={"wheel": str(wheel)})
    assert ok.status_code == 202, ok.text
    argv = " ".join(lances[0])
    assert "/tmp/pirate" not in argv and "--service forgemaster" in argv


def test_deux_gestes_dans_la_MEME_seconde_ne_sEcrasent_pas(instance, monkeypatch):
    """L'horodatage d'un run est à la SECONDE, et le handler est synchrone — donc servi par un fil du pool :
    deux requêtes peuvent y tomber ensemble. Avec un `mkdir(exist_ok=True)`, la seconde écrasait le
    `run.json` de la première (l'intention d'un run EN VOL, perdue) avant d'échouer de toute façon sur un nom
    d'unité déjà pris. Elle refuse maintenant en 409, et rien de l'autre n'est touché."""
    client, settings, wheel = instance
    _pas_de_vrai_systemd(monkeypatch)

    premier = client.post("/api/update/apply", json={"wheel": str(wheel)}).json()
    meta = (settings.home / update.UPDATES / premier["run"] / update.RUN_META)
    avant = meta.read_text(encoding="utf-8")
    # Le temps est gelé sur la seconde du premier run : la collision devient CERTAINE au lieu d'être une
    # course qu'on espère perdre. Un identifiant de run ne porte aucun `%`, donc `strftime` le rend verbatim.
    monkeypatch.setattr(update, "RUN_STAMP", premier["run"])

    r = client.post("/api/update/apply", json={"wheel": str(wheel)})

    assert r.status_code == 409
    assert "même seconde" in r.json()["detail"]
    assert meta.read_text(encoding="utf-8") == avant, "l'intention du run en vol a été écrasée"


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


# --- l'aire de dépôt, vue du réseau ---------------------------------------------------------------------

def test_le_depot_rend_ce_quil_faut_pour_POSER(instance):
    """Le contrat de bout en bout de cette phase tient en une ligne : le `path` rendu par le dépôt se
    repasse **tel quel** au préflight. Le chaînage est donc un test, pas une intention — sans lui, la route
    de dépôt serait une belle boîte qui ne branche sur rien."""
    client, settings, _ = instance
    octets = b"PK\x03\x04" + b"y" * 2048

    r = client.post("/api/update/wheels", files={"file": ("forgemaster-9.9.9.whl", octets)})

    assert r.status_code == 201, r.text
    corps = r.json()
    assert corps["size"] == len(octets)
    assert corps["sha256"] == hashlib.sha256(octets).hexdigest()
    assert corps["pruned"] == []
    assert Path(corps["path"]).read_bytes() == octets

    plan = client.get("/api/update/plan", params={"wheel": corps["path"]})
    assert plan.status_code == 200, plan.text
    assert corps["path"] in "\n".join(plan.json()["describe"])


def test_les_trois_refus_portent_leur_CODE_et_ne_rangent_rien(instance):
    """400/413/415 sont trois corrections différentes pour l'utilisateur, pas trois façons de dire non. Et
    dans les trois cas l'aire reste **vide** : un refus qui laisserait une trace serait un demi-dépôt."""
    client, settings, _ = instance

    traversant = client.post("/api/update/wheels", files={"file": ("../evil.whl", b"PK")})
    mauvais_type = client.post("/api/update/wheels", files={"file": ("notes.txt", b"PK")})

    assert traversant.status_code == 400 and "path-traversal" in traversant.json()["detail"]
    assert mauvais_type.status_code == 415 and ".whl" in mauvais_type.json()["detail"]
    assert update.list_wheels(settings)["wheels"] == []


def test_au_dela_de_la_borne_le_reseau_recoit_413(instance, monkeypatch):
    """La borne est déplacée pour le test, pas contournée : ce qui se mesure est le comportement AU bord,
    et poster 64 Mo à travers un client de test mesurerait la patience de la suite."""
    client, settings, _ = instance
    monkeypatch.setattr(update, "WHEEL_MAX_BYTES", 256)
    monkeypatch.setattr(update, "WHEEL_CHUNK", 64)

    r = client.post("/api/update/wheels", files={"file": ("gros.whl", b"x" * 4096)})

    assert r.status_code == 413 and "WHEEL_MAX_BYTES" in r.json()["detail"]
    # L'aire elle-même peut exister (elle a été créée pour recevoir) ; ce qui ne doit pas exister, c'est un
    # DÉPÔT — ni entier, ni à moitié. C'est la propriété qui compte, pas la présence du dossier racine.
    assert update.list_wheels(settings)["wheels"] == []
    assert list((settings.home / update.WHEELS).iterdir()) == []


def test_deux_depots_dans_la_meme_seconde_rendent_409(instance, monkeypatch):
    """Même code que la collision de `spawn`, et le même motif : un conflit TRANSITOIRE, rien n'a été
    touché. Un 500 enverrait chercher une panne là où il n'y a qu'une seconde partagée."""
    client, _, _ = instance
    premier = client.post("/api/update/wheels", files={"file": ("a.whl", b"PK")}).json()
    monkeypatch.setattr(update, "RUN_STAMP", premier["stamp"])

    r = client.post("/api/update/wheels", files={"file": ("b.whl", b"AUTRE")})

    assert r.status_code == 409 and "horodatage" in r.json()["detail"]
    assert Path(premier["path"]).read_bytes() == b"PK"


def test_la_liste_DIT_sa_politique_de_retention(instance):
    """`keep` voyage avec la liste. Une rétention qu'il faut lire dans le code n'est pas déclarée — et une
    aire qui rendrait 3 dépôts sans dire pourquoi se lit « c'est tout ce qui a été déposé »."""
    client, _, _ = instance
    client.post("/api/update/wheels", files={"file": ("a.whl", b"PK")})

    vue = client.get("/api/update/wheels").json()

    assert vue["keep"] == update.KEEP_WHEELS
    assert [w["name"] for w in vue["wheels"]] == ["a.whl"]
    assert vue["wheels"][0]["in_use"] is False


# --- l'aptitude, vue du réseau : un refus est un ÉTAT ---------------------------------------------------

def test_l_aptitude_rend_200_MEME_QUAND_TOUT_REFUSE(instance):
    """La propriété de cette route, et la seule qui compte. Un refus d'aptitude est un ÉTAT : la requête
    est bien formée, l'instance répond, et sa réponse est « non ». Le 409 reste la réponse de `/plan`, qui
    prévisualise une ACTION.

    Rendre 409 ici obligerait la surface qui l'affiche AU REPOS à traiter un état normal du produit comme
    une panne — et elle ne pourrait plus distinguer « je ne sais pas revenir » de « je n'ai pas pu te
    répondre », ce qui est exactement l'écart que `lib/updateLiaison` existe pour tenir."""
    client, settings, _wheel = instance
    # l'unité lance un venv EN DUR : LE cas de la fiche, une instance jamais migrée vers le lien stable
    unite = Path(os.environ["HOME"]) / ".config/systemd/user/forgemaster.service"
    unite.write_text(f"[Service]\nExecStart={settings.home}/venvs/dur/bin/forgemaster serve "
                     f"--host 127.0.0.1 --port 8700\n", encoding="utf-8")

    r = client.get("/api/update/aptitude")

    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["deployable"]["ok"] is False
    assert "un venv EN DUR" in corps["deployable"]["reason"]
    assert corps["reversible"]["ok"] is None, "non mesuré n'est pas non — sinon la page affiche DEUX refus"


def test_l_aptitude_ne_dit_RIEN_du_transitoire_la_ou_le_plan_refuse(instance):
    """La frontière, vue des deux surfaces à la fois. Un dispatch en vol fait 409 sur `/plan` — c'est
    « pas maintenant ». L'aptitude, elle, n'en sait rien et n'a pas à en savoir : elle répond « cette
    instance sait-elle revenir ? », une question dont la réponse ne vieillit pas en secondes."""
    from forgemaster.dispatch import jobs
    from forgemaster.projects import registry
    from forgemaster.roadmap import model

    client, settings, _wheel = instance
    conn = store.open_db(settings)
    registry.create_project(conn, settings, slug="atelier-fictif")
    model.add_feature(conn, project_slug="atelier-fictif", slug="f-fictive")
    tache = model.add_task(conn, feature_ref="atelier-fictif/f-fictive", slug="t-fictive")
    jobs.record_start(conn, task_id=tache["id"], worktree="/tmp/wt", session_id="s-fictive")
    conn.close()

    plan = client.get("/api/update/plan", params={"mode": "rollback"})
    r = client.get("/api/update/aptitude")

    assert plan.status_code == 409 and "dispatch en cours" in plan.json()["detail"]
    assert r.status_code == 200
    assert "dispatch" not in json.dumps(r.json()), "le transitoire n'a rien à faire dans une aptitude"
