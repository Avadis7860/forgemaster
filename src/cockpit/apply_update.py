#!/usr/bin/env python3
"""apply_update — pose un wheel en BLEU/VERT et **revient tout seul** si la nouvelle version ne sert pas.

Un instantané qu'il faut penser à restaurer n'est pas un retour arrière, c'est un lot de consolation : il
suppose que l'utilisateur constate la panne, la diagnostique et trouve le script. Ici la machine agit la
première — elle vérifie AVANT de toucher au vivant, et si le vivant ne répond plus, elle défait ce qu'elle
a fait sans qu'on le lui demande.

Quatre choix portent tout le reste :

- **Hors-processus, stdlib pure, zéro import `cockpit`** (même espèce que `restore.py`). Ce script survit à
  la bascule du venv qu'il opère, tourne sous le `python3` du SYSTÈME, et reste jouable à la main quand
  l'installation est cassée — c'est-à-dire exactement quand on en a besoin.
- **Bleu/vert : on prouve avant de toucher.** Le wheel est installé dans un venv NEUF, à côté, et on le fait
  servir sur un port et un `COCKPIT_HOME` jetables. Tant que cette étape n'est pas verte, l'instance vivante
  n'a **rien** subi : ni arrêt, ni migration, ni bascule.
- **L'instantané est pris à FROID, service arrêté, juste avant la bascule** — et par le cockpit ANCIEN
  (`<venv courant>/bin/cockpit snapshot create`), jamais réimplémenté ici. Il protège de la **migration
  avant** : la nouvelle version migre la base à sa première ouverture, et la base monte en *forward-only*
  (aucune down-migration n'existe). Sans instantané pris ici, la bascule est irréversible.
- **La bascule est un lien symbolique remplacé atomiquement**, jamais une réinstallation en place. Revenir
  en arrière coûte alors le même geste, dans l'autre sens — donc le retour arrière est aussi simple que
  l'aller, ce qui est la seule façon qu'il soit fiable.

Ce que la sonde en isolation prouve : *le wheel démarre et sert*. Ce qu'elle ne prouve **pas** : que ta
configuration et ta base tiennent encore — son `COCKPIT_HOME` est vierge. C'est la vérification EN VIVANT
(étape 6) qui couvre ça, et son échec est précisément ce qui déclenche le retour arrière.

Usage (le verbe `cockpit update apply` le lance ; ceci est aussi le chemin manuel) :

    python3 apply.py --wheel <fichier.whl> --home ~/.cockpit --link ~/.cockpit/current \
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

KEEP_VENVS = 2          # le courant + le précédent : de quoi rebasculer à la main si tout le reste a échoué
PROBE_TIMEOUT = 60.0    # démarrage d'un daemon FastAPI, venv froid inclus
RESTORE = "restore.py"


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
    cockpit = venv_dir / "bin" / "cockpit"
    if not cockpit.is_file():
        raise UpdateFailed(f"le wheel n'a pas posé de commande `cockpit` dans {venv_dir} — ce n'est pas un "
                           f"wheel de cockpit")
    return cockpit


def probe_isolated(cockpit: str | Path, sandbox: Path, log) -> dict:
    """Fait servir la nouvelle version sur un port et un `COCKPIT_HOME` JETABLES, et retourne l'identité de
    build qu'elle déclare (`/api/version`). C'est cette identité qui deviendra l'attendu du vivant : aucun
    manifeste servi, aucune signature, aucun réseau — le wheel est sa propre référence."""
    port = _free_port()
    env = {**os.environ,
           "COCKPIT_HOME": str(sandbox / "home"),
           "COCKPIT_PROJECTS_ROOT": str(sandbox / "projects")}
    sandbox.mkdir(parents=True, exist_ok=True)
    out = (sandbox / "serve.log").open("w")
    log(f"[3/6] sonde en ISOLATION sur 127.0.0.1:{port} (home jetable — le vivant n'est pas touché)")
    proc = subprocess.Popen(  # noqa: S603 (argv construit ici, pas de shell)
        [str(cockpit), "serve", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=out, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        if not _wait_health(base, PROBE_TIMEOUT, proc=proc):
            tail = "\n      ".join((sandbox / "serve.log").read_text(errors="replace").splitlines()[-15:])
            raise UpdateFailed(f"la nouvelle version ne sert pas en isolation.\n      {tail}")
        identity = _identity(_get_json(base, "/api/version"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        out.close()
    log(f"      ✓ elle sert — cockpit {identity['version']} ({identity['sha'] or 'build non tamponné'})")
    return identity


def take_snapshot(old_cockpit: Path, home: Path, log) -> Path:
    """Prend l'instantané par le cockpit **ANCIEN**, service arrêté. Réimplémenter la prise ici en ferait
    une seconde implémentation, donc une seule testée — et fatalement la mauvaise. Un ancien cockpit qui ne
    connaît pas le verbe `snapshot` fait échouer la MAJ ici, AVANT la bascule : on ne bascule jamais sur une
    version dont on ne saurait pas revenir."""
    proc = subprocess.run(  # noqa: S603
        # `--home` est porté par la SOUS-commande (parser `common` en parent), pas par la racine : le mettre
        # devant fait sortir argparse en usage. Constaté par l'acceptance, pas par relecture.
        [str(old_cockpit), "snapshot", "create", "--home", str(home)],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise UpdateFailed(f"impossible de prendre l'instantané avec {old_cockpit} "
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
        return False, (f"le vivant sert cockpit {live.get('version')!r}, attendu "
                       f"{expected.get('version')!r}")
    if expected.get("sha") is None or live.get("sha") is None:
        return True, (f"cockpit {live.get('version')} sert — provenance de build non comparable "
                      f"(wheel sans tampon), la version seule a été vérifiée")
    if live["sha"] != expected["sha"]:
        return False, f"le vivant sert le build {live['sha'][:12]}, attendu {expected['sha'][:12]}"
    return True, f"cockpit {live['version']} ({live['sha'][:12]}) sert"


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
        cockpit = build_blue(sys.executable, blue, wheel, log)
        expected = probe_isolated(cockpit, run_dir / "sandbox", log)
    except UpdateFailed as exc:
        return 1, f"MAJ refusée — {exc}", {**details, "impact": "aucun : le service n'a pas été touché"}
    details["attendu"] = expected

    log("[4/6] arrêt du service, puis instantané à froid (protège de la migration avant)")
    _systemctl(args, "stop", log)
    try:
        snap = take_snapshot(old_venv / "bin" / "cockpit", home, log)
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
    _restore(snap, home, log)
    _systemctl(args, "start", log)
    back = _wait_health(args.base_url, args.timeout)
    details["impact"] = "revenu à l'état d'avant (venv + données)"
    if not back:
        return 2, (f"MAJ échouée ({why}) — retour arrière effectué, mais l'instance ne répond TOUJOURS "
                   f"pas. Regarde le journal du service ; l'instantané est intact : {snap}"), details
    return 1, f"MAJ échouée ({why}) — l'instance est revenue à l'état d'avant, elle sert de nouveau", details


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    run_dir = Path(args.run_dir).expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)
    journal = (run_dir / "journal.log").open("a", encoding="utf-8", buffering=1)

    def log(msg: str) -> None:
        journal.write(msg + "\n")
        print(msg, flush=True)

    log(f"== MAJ lancée {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} — wheel {args.wheel}")
    try:
        rc, verdict, details = apply(args, log)
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


def _restore(snapshot: Path, home: Path, log) -> None:
    """Restaure par le script FIGÉ DANS l'instantané — celui écrit en même temps que son manifeste, donc
    celui qui le comprend. C'est aussi le chemin de secours manuel : on exerce ici ce qu'on documente."""
    script = snapshot / RESTORE
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(script), "--snapshot", str(snapshot), "--home", str(home)],
        capture_output=True, text=True, check=False)
    for line in proc.stdout.strip().splitlines():
        log(f"      {line}")
    if proc.returncode != 0:
        log(f"      ⚠ restauration rc={proc.returncode} : {proc.stderr.strip()}")


def _verify_live(base: str, expected: dict, timeout: float, log) -> tuple[bool, str]:
    if not _wait_health(base, timeout):
        return False, f"le daemon ne répond pas sur {base} après {timeout:.0f} s"
    try:
        live = _identity(_get_json(base, "/api/version"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"/api/version illisible sur le vivant : {exc}"
    return matches(expected, live)


def _wait_health(base: str, timeout: float, *, proc: subprocess.Popen | None = None) -> bool:
    """Attend `/health`. Si le processus sondé meurt, on n'attend pas la fin du délai : un échec rapide est
    un meilleur service qu'un long silence."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as r:  # noqa: S310 (http local)
                if r.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    return False


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
    """Garde les `KEEP_VENVS` venvs les plus récents (le courant en fait toujours partie). Une capacité qui
    remplit le disque en silence est une régression, pas une garantie."""
    kept = {p.resolve() for p in keep}
    olds = [p for p in sorted(root.iterdir(), reverse=True) if p.is_dir() and p.resolve() not in kept]
    for stale in olds[max(0, KEEP_VENVS - len(kept)):]:
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
    p.add_argument("--wheel", required=True, help="le wheel à poser (fichier local, aucun réseau)")
    p.add_argument("--home", required=True, help="racine d'état du cockpit (~/.cockpit)")
    p.add_argument("--link", required=True, help="le lien stable que l'unité systemd lance")
    p.add_argument("--venvs", help="racine des venvs (défaut : <home>/venvs)")
    p.add_argument("--run-dir", required=True, help="où écrire journal.log et result.json")
    p.add_argument("--base-url", required=True, help="URL du daemon vivant (déduite de l'unité)")
    p.add_argument("--service", default="cockpit", help="nom de l'unité systemd")
    p.add_argument("--systemctl", default="systemctl", help="binaire systemctl (injectable pour les tests)")
    p.add_argument("--scope", default="user", choices=["user", "system"])
    p.add_argument("--timeout", type=float, default=PROBE_TIMEOUT, help="délai de réponse du vivant (s)")
    args = p.parse_args(argv)
    args.venvs = args.venvs or str(Path(args.home).expanduser() / "venvs")
    return args


if __name__ == "__main__":
    sys.exit(main())
