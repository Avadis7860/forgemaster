"""restore — remet un instantané en place. **Stdlib pure, zéro import `forgemaster`.**

C'est l'**unique** implémentation de la restauration : `forgemaster snapshot restore` la *lance*, ne la refait
pas. Deux implémentations, c'est une seule testée — et fatalement la mauvaise le jour où ça compte.

Le même fichier voyage à trois endroits, pour trois raisons distinctes :

- **dans le forgemaster installé** (`forgemaster/restore.py`) — c'est là qu'il est écrit, relu et testé ;
- **dans chaque instantané** — un vieil instantané reste restaurable par le script écrit en même temps que
  son manifeste, même si le produit a changé de format depuis ;
- **à `<home>/restore.py`**, chemin **stable** — un chemin stable est ce qu'on peut écrire dans un message
  d'erreur ou retrouver six mois plus tard ; un chemin daté, non.

D'où la contrainte « stdlib pure » : il doit tourner avec le `python3` du système sur une instance dont le
venv est cassé — précisément la situation où on restaure.

Trois choix portent le reste :

- **Le `-wal` du voisinage part avec l'ancienne base.** Écraser le seul `forgemaster.db` en laissant son
`-wal`
  ne restaure PAS : SQLite rejoue le journal par-dessus le fichier remis et **ressuscite ce qu'on voulait
  défaire** (vérifié le 2026-08-02, y compris après un processus tué : la ligne écrite après l'instantané
  revient, sans la moindre erreur). Un retour arrière qui ne retourne pas en arrière, en silence, est pire
  qu'un échec bruyant.
- **Tout est vérifié AVANT la première écriture** (manifeste, présence, empreintes). Un instantané abîmé
  fait échouer la restauration sans avoir touché à l'instance, jamais à moitié.
- **Rien n'est détruit, tout est mis de côté** dans `<home>/before-restore-<horodatage>/`. Restaurer le
  mauvais instantané reste une erreur rattrapable — et une erreur rattrapable est ce qui rend le geste
  praticable par quelqu'un qui doute.
- **Le binaire et la donnée forment une seule unité de retour arrière.** Remettre une base que le
  forgemaster en place ne sait pas lire la rend illisible *définitivement* — la base monte en forward-only.
  Le garde de compatibilité (§« garde de compatibilité ») vit donc ici, avant la première écriture, et
  **nulle part ailleurs** : deux implémentations, c'est une seule testée.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = 1
MANIFEST = "manifest.json"
ASIDE_PREFIX = "before-restore-"
# Un `.db` SQLite ne voyage jamais seul : ces deux-là appartiennent à l'ancienne base, pas à la remise.
SIDECARS = ("-wal", "-shm")
# `<home>/current` — le lien vers le venv ACTIF (`service.LINK_NAME`). Redit ici, et pas importé : ce module
# est stdlib-pur par contrat. Le couplage est nommé pour qu'un renommage là-bas se voie ici.
STABLE_LINK = "current"
# Les modules qui portent `SCHEMA_VERSION`, du plus récent au plus ancien. Le second est d'avant le
# renommage `cockpit` → `forgemaster` : le binaire dangereux est par construction l'ANCIEN, donc c'est lui
# qu'il faut savoir interroger.
SCHEMA_MODULES = ("forgemaster.db.schema", "cockpit.db.schema")
PROBE_TIMEOUT = 30.0


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


# --- garde de compatibilité (aucune écriture) -------------------------------------------------------
#
# La base monte en FORWARD-ONLY : aucune down-migration n'existe et il n'en sera pas écrit. Une base de
# schéma neuf sous un binaire ancien est donc illisible, et rien ne peut la sauver. Or le retour arrière
# d'une MAJ demande DEUX gestes — rebasculer le lien ET restaurer l'instantané — et rien ne vérifiait leur
# cohérence : `db/store.migrate()` ne réagit que si la base est EN RETARD (`user_version < SCHEMA_VERSION`),
# une base trop neuve passe en silence.
#
# La comparaison porte sur le SCHÉMA, ni sur la version produit ni sur le SHA de build : deux versions
# peuvent partager un schéma (refuser dessus produirait des refus faux) et un SHA n'ordonne rien. Heureuse
# conséquence : le schéma se lit DANS le `.db` de l'instantané, donc le format d'instantané ne change pas
# (`SCHEMA` reste 1) et le garde protège aussi les instantanés déjà pris.

def snapshot_schema(snapshot_dir: Path, manifest: dict) -> int | None:
    """Schéma de la base **portée par l'instantané**. `None` si l'instantané n'en porte pas (l'instance ne
    l'avait pas à la prise) — il n'y a alors aucune base à rendre illisible, donc rien à garder."""
    for entry in manifest["entries"]:
        if str(entry["restore_to"]).endswith(".db"):
            return _user_version(snapshot_dir / entry["name"])
    return None


def installed_schema(home: Path) -> int | None:
    """Schéma **maximum** que sait lire le forgemaster actuellement en place, ou `None` si on ne peut pas le
    savoir (lien mort, venv sans python, paquet illisible)."""
    return python_schema(home / STABLE_LINK / "bin" / "python")


def python_schema(python: Path) -> int | None:
    """Schéma maximum que sait lire le forgemaster d'**un** venv, `None` si on ne peut pas le savoir.

    On demande sa **constante** au python du venv, jamais un verbe CLI : un verbe neuf ne serait porté
    que par les binaires POSTÉRIEURS à ce garde, alors que le binaire dangereux est l'ancien. `SCHEMA_VERSION`
    existe, elle, depuis le premier commit du dépôt.

    Séparée d'`installed_schema` le 2026-08-06 : `snapshot.list_snapshots` pose la même question à **chaque**
    venv retenu pour dire si un instantané est encore restaurable. Une seconde sonde aurait divergé de
    celle-ci le jour où l'une des deux évolue — et c'est ce garde-là qui compte."""
    if not python.is_file():
        return None
    code = (
        "import sys\n"
        f"for name in {SCHEMA_MODULES!r}:\n"
        "    try:\n"
        "        mod = __import__(name, fromlist=['SCHEMA_VERSION'])\n"
        "    except Exception:\n"
        "        continue\n"
        "    print(mod.SCHEMA_VERSION)\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n"
    )
    try:
        proc = subprocess.run([str(python), "-c", code],  # noqa: S603 (argv construit ici, pas de shell)
                              capture_output=True, text=True, check=False, timeout=PROBE_TIMEOUT,
                              env=_probe_env())
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def check_compatibility(snap: int | None, installed: int | None, *, allow_unverified: bool = False) -> None:
    """Refuse une remise que le binaire en place ne saurait pas lire. Trois issues, toutes explicites :
    **compatible** (on passe), **incompatible** (refus sec — la panne est certaine), **indéterminable**
    (refus, mais le message dit la porte : un refus qui bloque le secours dans la situation même qu'il sert
    serait un check défaillant, un simple avertissement ne tiendrait plus l'invariant)."""
    if snap is None:
        return                              # aucune base dans l'instantané : rien à rendre illisible
    if installed is None:
        if allow_unverified:
            return
        raise RestoreError(
            f"impossible de savoir quel schéma de base le forgemaster en place sait lire "
            f"(<home>/{STABLE_LINK} : lien mort, venv sans python, ou paquet illisible), et cet instantané "
            f"porte le schéma {snap}. La remettre à l'aveugle peut rendre la base définitivement illisible : "
            f"la base monte en forward-only, aucune down-migration n'existe.\n"
            f"  → rebascule d'abord <home>/{STABLE_LINK} vers le venv d'alors, puis relance\n"
            f"  → ou, si tu sais ce que tu fais : --allow-unverified-binary")
    if snap > installed:
        raise RestoreError(
            f"cet instantané porte une base de schéma {snap}, et le forgemaster en place ne sait lire que "
            f"jusqu'au schéma {installed}. La remettre la rendrait illisible pour lui, définitivement : la "
            f"base monte en forward-only, aucune down-migration n'existe.\n"
            f"  → rebascule <home>/{STABLE_LINK} vers le venv qui a PRIS cet instantané, puis relance\n"
            f"  → ou choisis un instantané plus ancien (`{sys.executable} {__file__}` les liste)")


def _probe_env() -> dict[str, str]:
    """L'environnement de la sonde, **débarrassé de ce qui pourrait lui faire importer un autre
    forgemaster**. Un venv trouve son `site-packages` par son `pyvenv.cfg` : il n'a besoin ni de
    `PYTHONPATH` ni de `PYTHONHOME`, et les hériter fait répondre la sonde sur le forgemaster de
    L'APPELANT au lieu de celui du venv sondé.

    Mesuré le 2026-08-06, pas déduit : un `bin/python` isolé (sans `pyvenv.cfg`) répondait `20` tant que
    l'appelant exportait un `PYTHONPATH`, et `None` sans lui. Ce garde décide d'une restauration
    irréversible — répondre juste par accident d'environnement n'est pas répondre."""
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}


def _user_version(db: Path) -> int | None:
    """`PRAGMA user_version` d'une base, en lecture seule. Toute erreur rend `None` — un fichier illisible
    est un schéma **indéterminable**, pas un schéma 0 (qui, lui, passerait le garde en silence)."""
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# --- restauration -----------------------------------------------------------------------------------

def restore(snapshot_dir: Path, *, home: Path | None = None, dry_run: bool = False,
            allow_unverified: bool = False) -> None:
    """Remet l'instantané dans `home` (défaut : le `home` inscrit au manifeste)."""
    snapshot_dir = Path(snapshot_dir).resolve()
    manifest = load_manifest(snapshot_dir)
    verify(snapshot_dir, manifest)
    target = Path(home).expanduser().resolve() if home else Path(manifest["home"])

    snap_schema = snapshot_schema(snapshot_dir, manifest)
    installed = installed_schema(target)
    check_compatibility(snap_schema, installed, allow_unverified=allow_unverified)

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
    if snap_schema is not None:
        print(f"  schéma   : {snap_schema} — " + (
            f"le forgemaster en place lit jusqu'au {installed}" if installed is not None
            else "binaire en place non interrogeable, garde de compatibilité FORCÉ"))
        # L'écart VERS LE BAS passe le garde — et c'est justement celui qu'on ne voit pas venir : la base
        # remise migrera en avant à la première ouverture, sans retour. L'annoncer comme un simple écart de
        # nombres, à l'instant où le geste devient irréversible, laissait l'utilisateur le lire comme un
        # détail. Même vocabulaire que l'état « données seules » de `snapshot list` (2026-08-06).
        if installed is not None and snap_schema < installed:
            print(f"  ⚠ DONNÉES SEULES : cette base sera migrée EN AVANT vers le schéma {installed} à la "
                  f"première ouverture.\n"
                  f"    Tu récupères tes données ; tu ne reviens PAS au forgemaster d'alors, et tu ne le\n"
                  f"    pourras plus (la base monte en forward-only). Pour revenir pour de bon, rebascule\n"
                  f"    d'abord <home>/{STABLE_LINK} vers le venv qui a PRIS cet instantané.")

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
    return Path(os.environ.get("FORGEMASTER_HOME") or "~/.forgemaster").expanduser().resolve()


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
        print(f"  {d.name}  {manifest['created_at']}  forgemaster {manifest['forgemaster']['version']}  "
        f"[{noms}]")
    print(f"\nrelancer en désignant celui voulu :\n  {sys.executable} {__file__} --snapshot {dirs[0]}")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="restore.py",
        description="Remet un instantané du forgemaster en place. Aucune dépendance : stdlib seule.")
    parser.add_argument("--snapshot", type=Path,
                        help="dossier de l'instantané (inutile si ce script est DANS l'instantané)")
    parser.add_argument("--home", type=Path, help="racine à réécrire (défaut : celle inscrite au manifeste)")
    parser.add_argument("--dry-run", action="store_true", help="dire ce qui serait fait, ne rien écrire")
    parser.add_argument("--allow-unverified-binary", action="store_true",
                        help="passer outre quand le schéma lisible par le forgemaster en place est "
                             "INDÉTERMINABLE (lien mort, venv cassé). N'annule PAS le refus d'une "
                             "incompatibilité constatée : celle-là est certaine, pas douteuse.")
    args = parser.parse_args(argv)

    snapshot_dir = args.snapshot or _snapshot_beside_script()
    if snapshot_dir is None:
        return _list_snapshots(_default_home(args.home))
    try:
        restore(snapshot_dir, home=args.home, dry_run=args.dry_run,
                allow_unverified=args.allow_unverified_binary)
    except RestoreError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
