#!/usr/bin/env python3
"""apply_update — pose un wheel en BLEU/VERT et **revient tout seul** si la nouvelle version ne sert pas.

Un instantané qu'il faut penser à restaurer n'est pas un retour arrière, c'est un lot de consolation : il
suppose que l'utilisateur constate la panne, la diagnostique et trouve le script. Ici la machine agit la
première — elle vérifie AVANT de toucher au vivant, et si le vivant ne répond plus, elle défait ce qu'elle
a fait sans qu'on le lui demande.

Quatre choix portent tout le reste :

- **Hors-processus, stdlib pure, zéro import `forgemaster`** (même espèce que `restore.py`). Ce script survit
à
  la bascule du venv qu'il opère, tourne sous le `python3` du SYSTÈME, et reste jouable à la main quand
  l'installation est cassée — c'est-à-dire exactement quand on en a besoin.
- **Bleu/vert : on prouve avant de toucher.** Le wheel est installé dans un venv NEUF, à côté, et on le fait
  servir sur un port et un `FORGEMASTER_HOME` jetables. Tant que cette étape n'est pas verte, l'instance
  vivante
  n'a **rien** subi : ni arrêt, ni migration, ni bascule.
- **L'instantané est pris à FROID, service arrêté, juste avant la bascule** — et par le forgemaster ANCIEN
  (`<venv courant>/bin/forgemaster snapshot create`), jamais réimplémenté ici. Il protège de la **migration
  avant** : la nouvelle version migre la base à sa première ouverture, et la base monte en *forward-only*
  (aucune down-migration n'existe). Sans instantané pris ici, la bascule est irréversible.
- **La bascule est un lien symbolique remplacé atomiquement**, jamais une réinstallation en place. Revenir
  en arrière coûte alors le même geste, dans l'autre sens — donc le retour arrière est aussi simple que
  l'aller, ce qui est la seule façon qu'il soit fiable.

Ce que la sonde en isolation prouve : *le wheel démarre et sert*. Ce qu'elle ne prouve **pas** : que ta
configuration et ta base tiennent encore — son `FORGEMASTER_HOME` est vierge. C'est la vérification EN VIVANT
(étape 6) qui couvre ça, et son échec est précisément ce qui déclenche le retour arrière.

Usage (le verbe `forgemaster update apply` le lance ; ceci est aussi le chemin manuel) :

    python3 apply.py --wheel <fichier.whl> --home ~/.forgemaster --link ~/.forgemaster/current \
                     --base-url http://127.0.0.1:8700 --run-dir <dossier de journal>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# LA politique de rétention, déclarée UNE fois et ici. Ici parce que ce module est stdlib-pur par contrat
# (il ne peut rien importer de `forgemaster`, cf. `test_apply_ne_depend_de_rien_du_forgemaster`) : c'est
# `snapshot.py` qui lit la constante, l'inverse serait impossible. Ce qui change n'est pas le disque
# consommé — les deux valeurs dérivées valent ce qu'elles valaient — mais que « jusqu'où on sait revenir »
# devienne un NOMBRE NOMMÉ, sur lequel les deux rétentions s'accordent au lieu de coïncider.
ROLLBACK_DEPTH = 1                  # de combien de crans `forgemaster update rollback` sait remonter
KEEP_VENVS = ROLLBACK_DEPTH + 1     # le courant + les crans joignables : rien de plus ne sert à revenir
# + la marge « la MAJ a échoué, j'ai restauré, j'ai retenté, ça a re-échoué ». Déclarée ICI et lue par
# `snapshot.KEEP` : le retour arrière volontaire doit savoir compter les crans d'instantanés pour refuser
# une cible que sa propre prise de sûreté détruirait — et il ne peut rien importer de `forgemaster`.
KEEP_SNAPSHOTS = ROLLBACK_DEPTH + 2
PROBE_TIMEOUT = 60.0    # démarrage d'un daemon FastAPI, venv froid inclus
RESTORE = "restore.py"
FORCE_FLAG = "--allow-unverified-binary"


class UpdateFailed(Exception):
    """Échec ARRÊTÉ avant la bascule : l'instance vivante n'a rien subi."""


# --- étapes -------------------------------------------------------------------------------------------

def build_blue(python: str, venv_dir: Path, wheel: Path, log) -> Path:
    """Crée un venv NEUF à côté et y installe le wheel. À côté, jamais en place : un processus ne remplace
    pas le wheel qu'il exécute, et surtout l'ancien venv doit rester intact pour pouvoir y revenir."""
    log(f"[1/6] venv neuf → {venv_dir}")
    _run([python, "-m", "venv", str(venv_dir)], log)
    log(f"[2/6] installation du wheel → {wheel.name}")
    _run([str(venv_dir / "bin" / "pip"), "install", "--quiet", "--upgrade", str(wheel)], log)
    forgemaster = venv_dir / "bin" / "forgemaster"
    if not forgemaster.is_file():
        raise UpdateFailed(f"le wheel n'a pas posé de commande `forgemaster` dans {venv_dir} — ce n'est pas "
        f"un "
                           f"wheel de forgemaster")
    return forgemaster


def probe_isolated(forgemaster: str | Path, sandbox: Path, log, *, step: str = "3/6") -> dict:
    """Fait servir la nouvelle version sur un port et un `FORGEMASTER_HOME` JETABLES, et retourne l'identité
    de
    build qu'elle déclare (`/api/version`). C'est cette identité qui deviendra l'attendu du vivant : aucun
    manifeste servi, aucune signature, aucun réseau — le wheel est sa propre référence."""
    port = _free_port()
    env = {**os.environ,
           "FORGEMASTER_HOME": str(sandbox / "home"),
           "FORGEMASTER_PROJECTS_ROOT": str(sandbox / "projects")}
    sandbox.mkdir(parents=True, exist_ok=True)
    out = (sandbox / "serve.log").open("w")
    log(f"[{step}] sonde en ISOLATION sur 127.0.0.1:{port} (home jetable — le vivant n'est pas touché)")
    proc = subprocess.Popen(  # noqa: S603 (argv construit ici, pas de shell)
        [str(forgemaster), "serve", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=out, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        served, why = _wait_health(base, PROBE_TIMEOUT, proc=proc)
        if not served:
            tail = "\n      ".join((sandbox / "serve.log").read_text(errors="replace").splitlines()[-15:])
            raise UpdateFailed(f"la nouvelle version ne sert pas en isolation — {why}.\n      {tail}")
        identity = _identity(_get_json(base, "/api/version"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        out.close()
    log(f"      ✓ elle sert — forgemaster {identity['version']} ({identity['sha'] or 'build non tamponné'})")
    return identity


def take_snapshot(old_forgemaster: Path, home: Path, log) -> Path:
    """Prend l'instantané par le forgemaster **ANCIEN**, service arrêté. Réimplémenter la prise ici en ferait
    une seconde implémentation, donc une seule testée — et fatalement la mauvaise. Un ancien forgemaster qui
    ne
    connaît pas le verbe `snapshot` fait échouer la MAJ ici, AVANT la bascule : on ne bascule jamais sur une
    version dont on ne saurait pas revenir."""
    proc = subprocess.run(  # noqa: S603
        # `--home` est porté par la SOUS-commande (parser `common` en parent), pas par la racine : le mettre
        # devant fait sortir argparse en usage. Constaté par l'acceptance, pas par relecture.
        [str(old_forgemaster), "snapshot", "create", "--home", str(home)],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise UpdateFailed(f"impossible de prendre l'instantané avec {old_forgemaster} "
                           f"(rc={proc.returncode}) — MAJ annulée.\n      {proc.stderr.strip()}")
    first = proc.stdout.splitlines()[0] if proc.stdout.strip() else ""
    if "→" not in first:
        raise UpdateFailed(f"`snapshot create` n'a pas dit où il a écrit : {first!r}")
    dest = Path(first.split("→", 1)[1].strip())
    if not (dest / "manifest.json").is_file():
        raise UpdateFailed(f"instantané sans manifeste ({dest}) — incomplet, donc inutilisable")
    log(f"      ✓ instantané → {dest}")
    return dest


def swap(link: Path, target: Path) -> None:
    """Remplace le lien stable de façon ATOMIQUE (`symlink` sur un temporaire + `os.replace`). Jamais
    `unlink` puis `symlink` : entre les deux, l'unité systemd pointerait le vide."""
    tmp = link.with_name(link.name + ".swap")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, link)


def matches(expected: dict, live: dict) -> tuple[bool, str]:
    """Le vivant sert-il bien la version qu'on vient de poser ? FONCTION PURE. Un build non tamponné
    (`sha=None`, checkout éditable) ne se compare pas — on le DIT au lieu de conclure au vert."""
    if live.get("version") != expected.get("version"):
        return False, (f"le vivant sert forgemaster {live.get('version')!r}, attendu "
                       f"{expected.get('version')!r}")
    if expected.get("sha") is None or live.get("sha") is None:
        return True, (f"forgemaster {live.get('version')} sert — provenance de build non comparable "
                      f"(wheel sans tampon), la version seule a été vérifiée")
    if live["sha"] != expected["sha"]:
        return False, f"le vivant sert le build {live['sha'][:12]}, attendu {expected['sha'][:12]}"
    return True, f"forgemaster {live['version']} ({live['sha'][:12]}) sert"


# --- orchestration ------------------------------------------------------------------------------------

def apply(args: argparse.Namespace, log) -> tuple[int, str, dict]:
    """Le geste complet. Retourne `(rc, verdict, détails)`. Trois issues, toutes explicites : **posée**
    (rc 0), **refusée avant bascule** (rc 1, rien n'a bougé), **revenue en arrière** (rc 1, l'instance est
    telle qu'avant)."""
    home, link = Path(args.home).expanduser(), Path(args.link).expanduser()
    wheel = Path(args.wheel).expanduser().resolve()
    old_venv = link.resolve()
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    blue = Path(args.venvs).expanduser() / stamp
    run_dir = Path(args.run_dir).expanduser()
    details: dict[str, object] = {"wheel": str(wheel), "venv_avant": str(old_venv), "venv_neuf": str(blue)}

    try:
        forgemaster = build_blue(sys.executable, blue, wheel, log)
        expected = probe_isolated(forgemaster, run_dir / "sandbox", log)
    except UpdateFailed as exc:
        return 1, f"MAJ refusée — {exc}", {**details, "impact": "aucun : le service n'a pas été touché"}
    details["attendu"] = expected

    log("[4/6] arrêt du service, puis instantané à froid (protège de la migration avant)")
    _systemctl(args, "stop", log)
    try:
        snap = take_snapshot(old_venv / "bin" / "forgemaster", home, log)
    except UpdateFailed as exc:
        _systemctl(args, "start", log)
        return 1, f"MAJ refusée — {exc}", {**details, "impact": "aucun : service relancé tel quel"}
    details["instantane"] = str(snap)

    log(f"[5/6] bascule du lien stable {link} → {blue.name}, puis redémarrage")
    swap(link, blue)
    _systemctl(args, "start", log)

    log("[6/6] vérification EN VIVANT (c'est ici que la configuration et la base réelles sont jugées)")
    live_ok, why = _verify_live(args.base_url, expected, args.timeout, log)
    if live_ok:
        log(f"      ✓ {why}")
        _purge_venvs(blue.parent, keep={blue, old_venv}, log=log)
        return 0, f"MAJ posée — {why}", details

    log(f"      ✗ {why}")
    log("      → RETOUR ARRIÈRE automatique (lien + instantané), sans rien te demander")
    _systemctl(args, "stop", log)
    swap(link, old_venv)
    if not _restore(snap, home, log):
        # La moitié « données » a manqué : le lien est sur l'ANCIEN binaire et la base porte l'état NEUF,
        # déjà migré. Ancien binaire sur données neuves = base illisible, l'unique état que l'invariant
        # interdit. On re-bascule EN AVANT : le binaire neuf sait lire ces données-là, l'instance sert.
        log("      ✗ la restauration a échoué — RE-BASCULE en avant : un binaire ancien sur des données "
            "neuves est le seul état interdit")
        swap(link, blue)
        _systemctl(args, "start", log)
        return 2, (f"MAJ échouée ({why}) ET retour arrière incomplet : les données n'ont pas été remises. "
                   f"Le lien est resté sur la version NEUVE, qui sait lire la base. L'instantané est "
                   f"intact : {snap}"),\
            {**details, "impact": "aucune moitié : le lien est revenu sur la version neuve"}
    _systemctl(args, "start", log)
    back, back_why = _wait_health(args.base_url, args.timeout)
    details["impact"] = "revenu à l'état d'avant (venv + données)"
    if not back:
        return 2, (f"MAJ échouée ({why}) — retour arrière effectué, mais l'instance ne sert TOUJOURS pas : "
                   f"{back_why}. L'instantané est intact : {snap}"), details
    return 1, f"MAJ échouée ({why}) — l'instance est revenue à l'état d'avant, elle sert de nouveau", details


def rollback(args: argparse.Namespace, log) -> tuple[int, str, dict]:
    """Le retour arrière **volontaire** — symétrique de l'aller, pas un second mécanisme.

    Chaque étape réutilise la fonction que `apply` exerce déjà (`probe_isolated`, `take_snapshot`, `swap`,
    `_restore`, `_verify_live`, `_systemctl`). Un retour arrière qui ne partage pas le code de l'aller est un
    chemin qu'on ne joue qu'en catastrophe — donc jamais joué pour de vrai avant le jour où il compte.

    Trois issues, toutes explicites : **revenu** (rc 0), **refusé avant le premier geste** (rc 1, rien n'a
    bougé), **revenu du retour** (rc 1, l'instance est telle qu'avant le retour arrière)."""
    home, link = Path(args.home).expanduser(), Path(args.link).expanduser()
    cible_venv = Path(args.target_venv).expanduser().resolve()
    cible_snap = Path(args.snapshot).expanduser().resolve()
    venv_courant = link.resolve()
    run_dir = Path(args.run_dir).expanduser()
    details: dict[str, object] = {"mode": "rollback", "venv_avant": str(venv_courant),
                                  "venv_cible": str(cible_venv), "instantane_cible": str(cible_snap)}
    intact = {**details, "impact": "aucun : le service n'a pas été touché"}

    try:
        _refuse_if_target_would_be_purged(cible_snap, log)
        # On prouve le binaire cible AVANT de toucher au vivant. Double effet : c'est l'`expected` de la
        # vérification finale, et ça prouve du même coup que l'ancien binaire sert encore — s'il ne sert
        # plus, revenir vers lui n'aurait aucun sens et rien n'a bougé.
        expected = probe_isolated(cible_venv / "bin" / "forgemaster", run_dir / "sandbox", log, step="1/5")
    except UpdateFailed as exc:
        return 1, f"retour arrière refusé — {exc}", intact
    details["attendu"] = expected

    log("[2/5] arrêt du service, puis instantané de SÛRETÉ à froid (sans lui, un retour raté serait sans "
        "retour)")
    _systemctl(args, "stop", log)
    try:
        surete = take_snapshot(venv_courant / "bin" / "forgemaster", home, log)
    except UpdateFailed as exc:
        _systemctl(args, "start", log)
        return 1, f"retour arrière refusé — {exc}", {**details, "impact": "aucun : service relancé tel quel"}
    details["instantane_surete"] = str(surete)

    log("[3/5] re-vérification de la cible : la prise de sûreté a consommé un cran de rétention")
    if not (cible_snap / "manifest.json").is_file():
        _systemctl(args, "start", log)
        return 1, (f"retour arrière refusé — l'instantané cible {cible_snap.name} a disparu pendant la prise "
                   f"de sûreté. L'instantané de sûreté, lui, est intact : {surete}"), \
            {**details, "impact": "aucun : service relancé tel quel"}

    # L'ORDRE est contraint et non négociable : le lien D'ABORD, la restauration ENSUITE. `restore` interroge
    # `<home>/current` pour savoir quel schéma le binaire en place sait lire ; inversé, il verrait le binaire
    # NEUF et refuserait une restauration pourtant légitime. Cf. `tests/test_rollback.py`, qui lit cet ordre
    # dans le code même — le risque n'est pas l'appel d'aujourd'hui, c'est la simplification de demain.
    log(f"[4/5] bascule du lien {link} → {cible_venv.name}, PUIS restauration de {cible_snap.name}")
    try:
        swap(link, cible_venv)
    except OSError as exc:
        # La PREMIÈRE moitié a échoué : on n'entame pas la seconde. Un lien qui n'a pas bougé et des données
        # non touchées, c'est une instance intacte — la seule issue acceptable quand le geste ne peut pas
        # être complet.
        _systemctl(args, "start", log)
        return 1, (f"retour arrière refusé — la bascule du lien a échoué ({exc}) : AUCUNE restauration n'a "
                   f"été tentée, l'instance repart telle qu'elle était"), \
            {**details, "impact": "aucun : service relancé tel quel"}
    if not _restore(cible_snap, home, log):
        log("      ✗ la restauration a échoué — RE-BASCULE en avant : un binaire ancien sur des données "
            "neuves est le seul état interdit")
        swap(link, venv_courant)
        _systemctl(args, "start", log)
        return 1, ("retour arrière échoué — les données n'ont pas été remises, et le lien a été RE-basculé "
                   "sur la version courante : elle sait lire cette base. Aucune moitié n'est restée en "
                   "place."), {**details, "impact": "aucune moitié : l'instance est comme avant le geste"}
    _systemctl(args, "start", log)

    log("[5/5] vérification EN VIVANT contre l'identité prouvée à l'étape 1")
    live_ok, why = _verify_live(args.base_url, expected, args.timeout, log)
    if live_ok:
        log(f"      ✓ {why}")
        return 0, f"retour arrière effectué — {why}", \
            {**details, "impact": "revenu à l'état de l'instantané (venv + données)"}

    log(f"      ✗ {why}")
    log("      → RETOUR DU RETOUR : l'instance est remise telle qu'elle était il y a une minute")
    _systemctl(args, "stop", log)
    swap(link, venv_courant)
    # Ici le lien est DÉJÀ revenu sur le binaire courant, qui lit le schéma le plus haut : même si la remise
    # de la sûreté échoue, la base en place reste lisible par lui. Rien à compenser — mais l'échec se DIT,
    # parce que les données ne sont alors pas celles qu'on croit.
    remis = _restore(surete, home, log)
    _systemctl(args, "start", log)
    back, back_why = _wait_health(args.base_url, args.timeout)
    details["impact"] = ("revenu à l'état d'AVANT le retour arrière (venv + données)" if remis else
                         "lien revenu, mais les DONNÉES d'avant le geste n'ont pas pu être remises")
    if not remis:
        return 2, (f"retour arrière échoué ({why}), et la remise de l'instantané de sûreté a échoué elle "
                   f"aussi. Le lien est revenu sur la version courante, qui sait lire la base en place ; "
                   f"l'instantané de sûreté est intact : {surete}"), details
    if not back:
        return 2, (f"retour arrière échoué ({why}) — l'instance a été remise comme avant, mais elle ne sert "
                   f"TOUJOURS pas : {back_why}. L'instantané de sûreté est intact : {surete}"), details
    return 1, (f"retour arrière échoué ({why}) — l'instance est revenue à son état d'avant, elle sert de "
               f"nouveau"), details


def _refuse_if_target_would_be_purged(cible: Path, log) -> None:
    """La prise de sûreté consomme un cran de rétention et déclenche la purge : elle peut donc **détruire la
    cible du retour arrière**. On refuse AVANT le premier geste plutôt que de le découvrir entre les deux
    moitiés — un refus coûte une relance, une cible détruite ne se rattrape pas.

    `snapshot._purge` garde les `KEEP_SNAPSHOTS - 1` plus récents plus la prise en cours : la cible survit
    donc si, et seulement si, moins de `KEEP_SNAPSHOTS - 1` instantanés complets lui sont postérieurs."""
    root = cible.parent
    if not root.is_dir():
        raise UpdateFailed(f"{root} n'existe pas — aucun instantané à remettre")
    complets = [d for d in root.iterdir() if d.is_dir() and (d / "manifest.json").is_file()]
    posterieurs = [d for d in complets if d.name > cible.name]
    if len(posterieurs) >= KEEP_SNAPSHOTS - 1:
        raise UpdateFailed(
            f"la prise de sûreté purgerait {cible.name}, la cible même de ce retour arrière : "
            f"{len(posterieurs)} instantanés lui sont postérieurs et la rétention n'en garde "
            f"{KEEP_SNAPSHOTS}.\n"
            f"      → vise un instantané plus récent (`forgemaster snapshot list` les classe)\n"
            f"      → ou rebascule <home>/current à la main, puis "
            f"`forgemaster snapshot restore {cible.name}`")
    log(f"      ✓ {cible.name} survivra à la prise de sûreté "
        f"({len(posterieurs)} instantané(s) postérieur(s))")


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    run_dir = Path(args.run_dir).expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)
    journal = (run_dir / "journal.log").open("a", encoding="utf-8", buffering=1)

    def log(msg: str) -> None:
        journal.write(msg + "\n")
        print(msg, flush=True)

    quoi = (f"MAJ lancée {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} — wheel {args.wheel}"
            if args.mode == "apply" else
            f"RETOUR ARRIÈRE lancé {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} — "
            f"vers {args.target_venv} + {args.snapshot}")
    log(f"== {quoi}")
    try:
        rc, verdict, details = (apply if args.mode == "apply" else rollback)(args, log)
    except Exception as exc:                                    # noqa: BLE001 (verdict, jamais de trace nue)
        rc, verdict, details = 2, f"échec inattendu : {exc!r}", {}
    log(f"== {verdict}")
    _write_json(run_dir / "result.json", {"rc": rc, "verdict": verdict, **details})
    journal.close()
    return rc


# --- utilitaires --------------------------------------------------------------------------------------

def _systemctl(args: argparse.Namespace, action: str, log) -> None:
    cmd = [args.systemctl, *(["--user"] if args.scope == "user" else []), action, args.service]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.returncode != 0:
        log(f"      ⚠ {' '.join(cmd)} → rc={proc.returncode} {proc.stderr.strip()}")


def _restore(snapshot: Path, home: Path, log) -> bool:
    """Restaure par le script FIGÉ DANS l'instantané — celui écrit en même temps que son manifeste, donc
    celui qui le comprend. C'est aussi le chemin de secours manuel : on exerce ici ce qu'on documente.

    **Rend `False` au lieu d'avaler l'échec** (2026-08-06) : les deux gestes du retour arrière forment une
    unité, et un appelant qui ne sait pas que la moitié « données » a manqué laisse l'instance avec un
    binaire ancien sur des données neuves — exactement la moitié que l'invariant interdit.

    On lui passe `--allow-unverified-binary` **quand il le connaît**. Ce n'est pas un affaiblissement du garde
    de compatibilité : ici le lien vient d'être rebasculé sur le venv qui a PRIS cet instantané (`apply` fait
    `swap(link, old_venv)` juste avant), donc la compatibilité est acquise par construction et le seul état
    que le garde pourrait rendre est *indéterminable* — un doute que cet appelant-ci, lui, a levé. Le refus
    d'une incompatibilité **constatée** reste, lui, absolu : la porte ne le couvre pas.

    Le drapeau n'est ajouté que si le script figé le porte : un instantané pris avant ce garde embarque un
    `restore.py` dont l'argparse sortirait en usage, et ferait échouer le retour arrière au pire moment."""
    script = snapshot / RESTORE
    cmd = [sys.executable, str(script), "--snapshot", str(snapshot), "--home", str(home)]
    if _supports_flag(script, FORCE_FLAG):
        cmd.append(FORCE_FLAG)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    for line in proc.stdout.strip().splitlines():
        log(f"      {line}")
    if proc.returncode != 0:
        log(f"      ⚠ restauration rc={proc.returncode} : {proc.stderr.strip()}")
        return False
    return True


def _supports_flag(script: Path, flag: str) -> bool:
    """Le script figé connaît-il ce drapeau ? Lu dans son texte, pas déduit d'une version : un instantané ne
    porte pas le numéro de version de son propre `restore.py`, et lancer `--help` pour le savoir coûterait un
    processus de plus au moment le moins opportun. Illisible → on ne le passe pas (prudent)."""
    try:
        return flag in script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _verify_live(base: str, expected: dict, timeout: float, log) -> tuple[bool, str]:
    ready, why = _wait_health(base, timeout)
    if not ready:
        return False, why
    try:
        live = _identity(_get_json(base, "/api/version"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"/api/version illisible sur le vivant : {exc}"
    return matches(expected, live)


def _wait_health(base: str, timeout: float,
                 *, proc: subprocess.Popen | None = None) -> tuple[bool, str]:
    """Attend `/health`, et rend **pourquoi** quand c'est non. Trois issues, pas deux :

    - **200** — l'instance sert ;
    - **503 portant `ready:false`** — elle a démarré et se déclare inservable : verdict IMMÉDIAT, avec son
      motif. Attendre la fin du délai pour conclure « ne répond pas » transformerait une réponse claire en
      silence, et c'est ce silence qui remonterait à l'utilisateur comme diagnostic ;
    - **rien** (connexion refusée, ou 503 d'une autre forme) — on attend : on ne conclut que sur notre
      propre contrat, jamais sur un 503 qu'on n'a pas écrit.

    Si le processus sondé meurt, on n'attend pas non plus : un échec rapide est un meilleur service qu'un
    long silence."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False, f"le processus sondé s'est arrêté avant de servir sur {base}"
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as r:  # noqa: S310 (http local)
                if r.status == 200:
                    return True, ""
        except urllib.error.HTTPError as exc:
            detail = _unservable_detail(exc)
            if detail is not None:
                return False, f"l'instance a démarré mais ne peut pas servir — {detail}"
            time.sleep(0.5)
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    return False, f"le daemon ne répond pas sur {base} après {timeout:.0f} s"


def _unservable_detail(exc: urllib.error.HTTPError) -> str | None:
    """Le `detail` d'un `/health` qui se déclare inservable, ou `None` si ce 503 n'est pas le nôtre.
    On exige la forme complète (`ready` à `false`) : un 503 de proxy, de reverse-proxy ou d'un autre service
    qui écouterait ce port ne doit pas se faire passer pour un verdict de l'instance."""
    if exc.code != 503:
        return None
    try:
        body = json.loads(exc.read())
    except (ValueError, OSError):
        return None
    if not isinstance(body, dict) or body.get("ready") is not False:
        return None
    return str(body.get("detail") or "sans motif")


def _get_json(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=10) as r:  # noqa: S310 (http local)
        data = json.loads(r.read())
    return data if isinstance(data, dict) else {}


def _identity(payload: dict) -> dict:
    return {"version": payload.get("version"), "sha": payload.get("sha")}


def _run(cmd: list[str], log) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-15:]
        raise UpdateFailed(f"`{' '.join(cmd[:3])}…` a échoué (rc={proc.returncode})\n      "
                           + "\n      ".join(tail))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _purge_venvs(root: Path, *, keep: set[Path], log) -> None:
    """Purge tout venv qui n'est **pas joignable par un retour arrière**. `keep` EST cette liste : l'appelant
    vient de faire la bascule, il la connaît. Une capacité qui remplit le disque en silence est une
    régression, pas une garantie.

    **On ne re-devine plus la liste par date de création.** L'ancienne formulation gardait « les `KEEP_VENVS`
    plus récents », ce qui donne le bon résultat à `ROLLBACK_DEPTH = 1` — et seulement là, parce que `keep`
    remplit alors tout le quota et ne laisse aucune place à la date. À `DEPTH` supérieur elle aurait gardé le
    plus récent des NON-gardés, qui est typiquement un bleu ayant ÉCHOUÉ en vivant : précisément le venv vers
    lequel il ne faut pas revenir. « Le plus récent » et « le cran d'avant » sont deux ordres différents, et
    ils ne coïncident qu'ici.

    Déclaration incohérente avec la politique → **on ne purge rien** et on le dit. Supprimer sur une liste
    qu'on ne comprend pas est le seul résultat irréversible de cette fonction."""
    kept = {p.resolve() for p in keep}
    if len(kept) != KEEP_VENVS:
        log(f"      ⚠ purge sautée : {len(kept)} venv(s) déclarés joignables, la politique en attend "
            f"{KEEP_VENVS} (ROLLBACK_DEPTH={ROLLBACK_DEPTH}) — rien n'a été supprimé")
        return
    for stale in sorted(root.iterdir(), reverse=True):
        if stale.is_dir() and stale.resolve() not in kept:
            shutil.rmtree(stale, ignore_errors=True)
            log(f"      purge du venv {stale.name}")


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="apply.py",
        description="Pose un wheel en bleu/vert ; revient seul si le vivant ne sert pas.")
    # Un MODE, pas un second script. Le script est **recopié à chaque run** par `update.launch` : sa CLI n'a
    # aucune compatibilité ascendante à tenir, contrairement à `restore.py` qui voyage dans les instantanés
    # (c'est pour ça que `_supports_flag` existe — et il ne s'applique pas ici).
    p.add_argument("--mode", choices=["apply", "rollback"], default="apply",
                   help="poser un wheel (apply) ou revenir vers un venv + un instantané (rollback)")
    p.add_argument("--wheel", help="le wheel à poser (--mode apply ; fichier local, aucun réseau)")
    p.add_argument("--target-venv", help="le venv vers lequel revenir (--mode rollback)")
    p.add_argument("--snapshot", help="l'instantané à remettre (--mode rollback)")
    p.add_argument("--home", required=True, help="racine d'état du forgemaster (~/.forgemaster)")
    p.add_argument("--link", required=True, help="le lien stable que l'unité systemd lance")
    p.add_argument("--venvs", help="racine des venvs (défaut : <home>/venvs)")
    p.add_argument("--run-dir", required=True, help="où écrire journal.log et result.json")
    p.add_argument("--base-url", required=True, help="URL du daemon vivant (déduite de l'unité)")
    p.add_argument("--service", default="forgemaster", help="nom de l'unité systemd")
    p.add_argument("--systemctl", default="systemctl", help="binaire systemctl (injectable pour les tests)")
    p.add_argument("--scope", default="user", choices=["user", "system"])
    p.add_argument("--timeout", type=float, default=PROBE_TIMEOUT, help="délai de réponse du vivant (s)")
    args = p.parse_args(argv)
    # `required=` d'argparse ne sait pas dépendre d'un autre drapeau : on le fait ici, et `p.error` rend
    # l'usage complet plutôt qu'un `AttributeError` trois étapes plus loin.
    requis = {"apply": ("wheel",), "rollback": ("target_venv", "snapshot")}[args.mode]
    absents = [f"--{nom.replace('_', '-')}" for nom in requis if not getattr(args, nom)]
    if absents:
        p.error(f"--mode {args.mode} exige {', '.join(absents)}")
    args.venvs = args.venvs or str(Path(args.home).expanduser() / "venvs")
    return args


if __name__ == "__main__":
    sys.exit(main())
