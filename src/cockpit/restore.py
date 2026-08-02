"""restore — remet un instantané en place. **Stdlib pure, zéro import `cockpit`.**

C'est l'**unique** implémentation de la restauration : `cockpit snapshot restore` la *lance*, ne la refait
pas. Deux implémentations, c'est une seule testée — et fatalement la mauvaise le jour où ça compte.

Le même fichier voyage à trois endroits, pour trois raisons distinctes :

- **dans le cockpit installé** (`cockpit/restore.py`) — c'est là qu'il est écrit, relu et testé ;
- **dans chaque instantané** — un vieil instantané reste restaurable par le script écrit en même temps que
  son manifeste, même si le produit a changé de format depuis ;
- **à `<home>/restore.py`**, chemin **stable** — un chemin stable est ce qu'on peut écrire dans un message
  d'erreur ou retrouver six mois plus tard ; un chemin daté, non.

D'où la contrainte « stdlib pure » : il doit tourner avec le `python3` du système sur une instance dont le
venv est cassé — précisément la situation où on restaure.

Trois choix portent le reste :

- **Le `-wal` du voisinage part avec l'ancienne base.** Écraser le seul `cockpit.db` en laissant son `-wal`
  ne restaure PAS : SQLite rejoue le journal par-dessus le fichier remis et **ressuscite ce qu'on voulait
  défaire** (vérifié le 2026-08-02, y compris après un processus tué : la ligne écrite après l'instantané
  revient, sans la moindre erreur). Un retour arrière qui ne retourne pas en arrière, en silence, est pire
  qu'un échec bruyant.
- **Tout est vérifié AVANT la première écriture** (manifeste, présence, empreintes). Un instantané abîmé
  fait échouer la restauration sans avoir touché à l'instance, jamais à moitié.
- **Rien n'est détruit, tout est mis de côté** dans `<home>/before-restore-<horodatage>/`. Restaurer le
  mauvais instantané reste une erreur rattrapable — et une erreur rattrapable est ce qui rend le geste
  praticable par quelqu'un qui doute.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = 1
MANIFEST = "manifest.json"
ASIDE_PREFIX = "before-restore-"
# Un `.db` SQLite ne voyage jamais seul : ces deux-là appartiennent à l'ancienne base, pas à la remise.
SIDECARS = ("-wal", "-shm")


class RestoreError(Exception):
    """Refus explicite. Levée AVANT toute écriture — l'instance est intacte quand elle sort."""


# --- lecture et vérification (aucune écriture) ------------------------------------------------------

def load_manifest(snapshot_dir: Path) -> dict:
    """Lit le manifeste, ou refuse. Un dossier sans manifeste est une prise **interrompue** : le produire
    en dernier est ce qui rend l'incomplétude détectable ici plutôt que découverte à mi-restauration."""
    path = snapshot_dir / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise RestoreError(
            f"pas de {MANIFEST} dans {snapshot_dir} — prise interrompue, instantané incomplet") from None
    except ValueError as exc:
        raise RestoreError(f"{path} illisible (JSON invalide) : {exc}") from None
    if data.get("schema") != SCHEMA:
        raise RestoreError(
            f"schéma {data.get('schema')!r} inconnu — ce script lit le schéma {SCHEMA} et refuse de deviner")
    return data


def verify(snapshot_dir: Path, manifest: dict) -> None:
    """Chaque entrée est présente et son empreinte correspond. Tout est contrôlé d'abord, puis rapporté
    ensemble : celui qui restaure veut la liste complète des dégâts, pas le premier."""
    problems = []
    for entry in manifest["entries"]:
        src = snapshot_dir / entry["name"]
        if not src.is_file():
            problems.append(f"{entry['name']} : déclaré au manifeste mais absent de l'instantané")
        elif sha256(src) != entry["sha256"]:
            problems.append(f"{entry['name']} : empreinte différente du manifeste (fichier altéré)")
    if problems:
        raise RestoreError(
            "instantané inutilisable — rien n'a été touché :\n  - " + "\n  - ".join(problems))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- restauration -----------------------------------------------------------------------------------

def restore(snapshot_dir: Path, *, home: Path | None = None, dry_run: bool = False) -> None:
    """Remet l'instantané dans `home` (défaut : le `home` inscrit au manifeste)."""
    snapshot_dir = Path(snapshot_dir).resolve()
    manifest = load_manifest(snapshot_dir)
    verify(snapshot_dir, manifest)
    target = Path(home).expanduser().resolve() if home else Path(manifest["home"])

    # Ce qui quitte l'instance : les entrées remplacées ET celles qui n'existaient pas à la prise (les
    # remettre à l'état d'alors, c'est aussi les RETIRER — sinon la restauration est partielle en silence).
    replaced = [p for e in manifest["entries"] for p in _with_sidecars(target / e["restore_to"])]
    removed = [p for name in manifest.get("absent", []) for p in _with_sidecars(target / name)]

    print(f"instantané : {snapshot_dir}")
    print(f"home       : {target}" + ("" if home is None else "  (imposé en ligne de commande)"))
    for entry in manifest["entries"]:
        print(f"  remet    : {entry['restore_to']}  ({entry['sha256'][:12]}…, mode {entry['mode']})")
    for path in removed:
        print(f"  retire   : {path.name}  (absent à la prise — l'instance ne l'avait pas)")
    for path in replaced:
        if path.name.endswith(SIDECARS):
            print(f"  écarte   : {path.name}  (journal de l'ANCIENNE base — le laisser annulerait la remise)")
    print(f"  intact   : {', '.join(manifest['excluded'])}")

    if dry_run:
        print("\n(--dry-run : rien n'a été écrit)")
        return

    aside = _aside_dir(target)
    for path in replaced + removed:
        dest = aside / path.relative_to(target)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, dest)              # même système de fichiers : déplacement atomique, zéro copie

    for entry in manifest["entries"]:
        dest = target / entry["restore_to"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copyfile(snapshot_dir / entry["name"], tmp)
        os.chmod(tmp, int(entry["mode"], 8))
        os.replace(tmp, dest)               # jamais de fichier à moitié écrit sous le nom final

    print(f"\n✓ restauré depuis {snapshot_dir.name}")
    print(f"  l'état précédent n'est pas détruit : {aside}")


def _with_sidecars(path: Path) -> list[Path]:
    """Le fichier et ses journaux SQLite, s'ils existent. Appliqué à toute entrée : un `-wal` à côté d'un
    fichier plat n'existe pas, et le vérifier coûte moins cher que de savoir lesquelles sont des bases."""
    candidates = (path, *(path.with_name(path.name + suffix) for suffix in SIDECARS))
    return [p for p in candidates if p.exists()]


def _aside_dir(home: Path) -> Path:
    """Où va l'état remplacé. `mkdir` sans `exist_ok` **est** le verrou : deux restaurations dans la même
    seconde ne se recouvrent pas."""
    base = ASIDE_PREFIX + datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    for suffix in ("", *(f"-{n}" for n in range(1, 100))):
        try:
            (cand := home / f"{base}{suffix}").mkdir(parents=True)
        except FileExistsError:
            continue
        return cand
    raise RestoreError(f"100 restaurations dans la même seconde sous {home} — refus de deviner un nom")


# --- ligne de commande ------------------------------------------------------------------------------

def _snapshot_beside_script() -> Path | None:
    """La copie figée DANS l'instantané se restaure elle-même, sans argument : `python3 restore.py`."""
    here = Path(__file__).resolve().parent
    return here if (here / MANIFEST).is_file() else None


def _default_home(explicit: Path | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    here = Path(__file__).resolve().parent          # la copie à chemin stable vit DANS le home
    if (here / "snapshots").is_dir():
        return here
    return Path(os.environ.get("COCKPIT_HOME") or "~/.cockpit").expanduser().resolve()


def _list_snapshots(home: Path) -> int:
    """Aucun instantané désigné : on montre lesquels existent et on s'arrête. Choisir le plus récent
    « pour rendre service » écraserait un état vivant sur une supposition — ça se demande."""
    root = home / "snapshots"
    dirs = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True) if root.is_dir() else []
    if not dirs:
        print(f"aucun instantané sous {root}", file=sys.stderr)
        return 2
    print(f"instantanés sous {root} (du plus récent au plus ancien) :")
    for d in dirs:
        try:
            manifest = load_manifest(d)
        except RestoreError as exc:
            print(f"  {d.name}  ⚠ INUTILISABLE — {str(exc).splitlines()[0]}")
            continue
        noms = ", ".join(e["name"] for e in manifest["entries"])
        print(f"  {d.name}  {manifest['created_at']}  cockpit {manifest['cockpit']['version']}  [{noms}]")
    print(f"\nrelancer en désignant celui voulu :\n  {sys.executable} {__file__} --snapshot {dirs[0]}")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="restore.py",
        description="Remet un instantané du cockpit en place. Aucune dépendance : stdlib seule.")
    parser.add_argument("--snapshot", type=Path,
                        help="dossier de l'instantané (inutile si ce script est DANS l'instantané)")
    parser.add_argument("--home", type=Path, help="racine à réécrire (défaut : celle inscrite au manifeste)")
    parser.add_argument("--dry-run", action="store_true", help="dire ce qui serait fait, ne rien écrire")
    args = parser.parse_args(argv)

    snapshot_dir = args.snapshot or _snapshot_beside_script()
    if snapshot_dir is None:
        return _list_snapshots(_default_home(args.home))
    try:
        restore(snapshot_dir, home=args.home, dry_run=args.dry_run)
    except RestoreError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
