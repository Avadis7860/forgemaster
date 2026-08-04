"""snapshot — instantané restaurable de l'état de l'utilisateur, pris AVANT une opération risquée (MAJ).

La base du forgemaster monte en **forward-only** (aucune down-migration n'est écrite) : le retour arrière
d'une
MAJ n'est donc PAS une migration inverse, c'est la **restauration** d'un instantané pris juste avant. Sans
cette capacité le produit n'a aucun retour arrière du tout — d'où ce module.

Trois choix portent tout le reste :

- **`VACUUM INTO` pour la base, jamais un `cp`.** `db/store.py` pose `PRAGMA journal_mode = WAL` : *« if a
  database file is separated from its WAL file, then transactions that were previously committed to the
  database might be lost, or the database file might become corrupted »* (doc SQLite, `wal.html`). `VACUUM
  INTO` rend au contraire *« a consistent snapshot of the original database »* en **un seul fichier**, et
  *« VACUUM (but not VACUUM INTO) is a write operation »* — aucun verrou d'écriture pris sur le vivant.
- **Le périmètre est DÉCLARÉ (`ENTRIES`/`EXCLUDED_IN_HOME`), jamais inféré d'un `cp -r`.** Une exclusion tue
  se fait « compléter » par réflexe de symétrie à la session suivante : chacune voyage donc **dans le
  manifeste**, pour que la restauration puisse dire ce qu'elle ne remettra pas.
- **`manifest.json` est écrit EN DERNIER.** Un dossier sans manifeste est un instantané **incomplet**, donc
  invalide — il doit se lire comme tel (`list_snapshots` le signale) et jamais se restaurer à moitié.

Trois états distincts et tous explicites : une entrée **prise** (`entries`), une entrée **absente à la
prise** (`absent` — l'instance ne l'avait pas), une entrée **délibérément hors périmètre** (`excluded`). On
ne découvre pas une exclusion en constatant un manque.
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

from forgemaster.config import Settings

SCHEMA = 1                 # un `restore.py` qui lit un schéma inconnu REFUSE honnêtement au lieu de deviner
KEEP = 3                   # assez pour « la MAJ a échoué, j'ai restauré, j'ai retenté, ça a re-échoué »
MANIFEST = "manifest.json"
RESTORE = "restore.py"     # l'unique implémentation de la restauration (stdlib pure) — cf. `restore.py`

# Le périmètre, déclaré : (chemin relatif à `home`, comment on le prend, mode du fichier posé).
ENTRIES: tuple[tuple[str, str, int], ...] = (
    ("forgemaster.db", "vacuum-into", 0o644),   # l'état : projets, features, tasks, jobs
    ("forgemaster.env", "copy", 0o600),         # le réglage de l'instance (bind, coffre, câblage MCP)
    ("secrets/store.enc", "copy", 0o600),   # le blob chiffré : sans lui, les credentials sont perdus
)

# Hors périmètre, délibérément. `master.key` : la restauration est locale, la clé y est déjà — l'embarquer ne
# produirait qu'un artefact qui, copié ailleurs, déchiffre tout. `logs/` : historique append-only, pas de
# l'état à remettre, et le copier avant CHAQUE MAJ la rendrait assez lente pour qu'on la saute. `snapshots/` :
# sinon l'instantané N+1 embarque les N précédents (croissance quadratique). `restore.py` : ce n'est pas de
# l'état, c'est de l'outillage re-posé à chaque prise — le restaurer reviendrait à revenir à un outil ancien.
# `venvs/`, `current`, `updates/` : du CODE et son journal, pas de l'état utilisateur — un instantané couvre
# la donnée, jamais le binaire. C'est précisément pour ça que le retour arrière d'une MAJ demande DEUX gestes
# (rebasculer le lien ET restaurer l'instantané) : les dire ici rend la frontière lisible dans l'artefact
# lui-même, au lieu de la laisser dans une doc que personne n'aura sous la main ce jour-là.
EXCLUDED_IN_HOME: tuple[str, ...] = (
    "secrets/master.key", "logs/", "snapshots/", RESTORE, "venvs/", "current", "updates/")


def snapshots_dir(settings: Settings) -> Path:
    """Racine des instantanés — sous `home`, donc exclue d'elle-même (cf. `EXCLUDED_IN_HOME`)."""
    return settings.home / "snapshots"


# --- prise ------------------------------------------------------------------------------------------

def create(settings: Settings, *, keep: int = KEEP) -> Path:
    """Prend un instantané et retourne son dossier. Purge les plus vieux **après** l'avoir complété : un
    échec en cours de prise ne doit jamais détruire un instantané antérieur encore bon."""
    dest = _new_dir(snapshots_dir(settings), datetime.now(UTC))
    entries: list[dict[str, str]] = []
    absent: list[str] = []
    for name, via, mode in ENTRIES:
        src = settings.home / name
        if not src.exists():
            absent.append(name)     # l'instance ne l'avait pas — ce n'est ni une prise ni une exclusion
            continue
        out = dest / name
        out.parent.mkdir(parents=True, exist_ok=True)
        if via == "vacuum-into":
            _vacuum_into(src, out)
        else:
            _copy_atomic(src, out)
        out.chmod(mode)
        entries.append({"name": name, "restore_to": name, "sha256": _sha256(out),
                        "mode": f"{mode:04o}", "via": via})

    _pose_restore_script(dest, settings.home)
    _write_json(dest / MANIFEST, {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forgemaster": _build_identity(),
        "home": str(settings.home),
        "entries": entries,
        "absent": absent,
        "excluded": [*EXCLUDED_IN_HOME, str(settings.projects_root)],
    })
    _purge(snapshots_dir(settings), keep=keep, spare=dest)
    return dest


def _vacuum_into(db_path: Path, dst: Path) -> None:
    """Copie cohérente de la base **sans** verrou d'écriture ni triplet `db`/`-wal`/`-shm` à transporter.
    La connexion n'ajuste **aucun** PRAGMA : on lit une base vivante, on ne la reconfigure pas. SQLite
    refuse une cible existante (`output file already exists`) — un instantané n'écrase jamais rien."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM INTO ?", (str(dst),))
    finally:
        conn.close()


def _copy_atomic(src: Path, dst: Path) -> None:
    """tmp + `os.replace` : jamais de fichier à moitié écrit sous le nom final (même seam que
    `service.set_env_keys`)."""
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _pose_restore_script(dest: Path, home: Path) -> None:
    """Dépose `restore.py` aux deux endroits qui comptent : **dans** l'instantané (un vieil instantané reste
    restaurable par le script écrit en même temps que son manifeste) et à `<home>/restore.py`, chemin
    **stable** — celui qu'on peut écrire dans un message d'erreur. Re-posé à chaque prise : sa présence ne
    dépend d'aucune étape d'installation, et un `restore.py` effacé revient tout seul."""
    src = Path(__file__).with_name(RESTORE)
    for target in (dest / RESTORE, home / RESTORE):
        _copy_atomic(src, target)
        target.chmod(0o755)


def _build_identity() -> dict[str, str | None]:
    """Version + SHA de build du forgemaster qui a PRIS l'instantané. `sha=None` en checkout éditable est un
    état honnête (le wheel n'a pas de tampon), pas une erreur — `read_stamp` ne lève jamais."""
    from forgemaster import __version__
    from forgemaster.build_provenance import read_stamp
    stamp = read_stamp()
    return {"version": __version__, "build_sha": stamp["sha"], "built_at": stamp["committed_at"]}


def _new_dir(root: Path, now: datetime) -> Path:
    """Dossier daté, UTC, sans `:` (hostile en shell et hors ext4). `mkdir` sans `exist_ok` **est** le
    verrou : deux prises dans la même seconde ne se marchent pas dessus."""
    base = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    root.mkdir(parents=True, exist_ok=True)
    for suffix in ("", *(f"-{n}" for n in range(1, 100))):
        try:
            (cand := root / f"{base}{suffix}").mkdir()
        except FileExistsError:
            continue
        return cand
    raise RuntimeError(f"100 instantanés dans la même seconde sous {root} — refus de deviner un nom")


def _purge(root: Path, *, keep: int, spare: Path) -> list[Path]:
    """Retire d'abord les dossiers **incomplets** (prise interrompue : aucun manifeste), puis ne garde que
    les `keep` instantanés complets les plus récents. `spare` n'est jamais touché."""
    if keep < 1:
        raise ValueError(f"keep doit valoir au moins 1, reçu {keep}")
    dropped: list[Path] = []
    complete: list[Path] = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        if d == spare:
            continue
        (complete if (d / MANIFEST).exists() else dropped).append(d)
    dropped.extend(complete[keep - 1:])     # `spare` occupe déjà une place parmi les `keep`
    for d in dropped:
        shutil.rmtree(d)
    return dropped


# --- lecture ----------------------------------------------------------------------------------------

def list_snapshots(settings: Settings) -> list[dict]:
    """Les instantanés du plus récent au plus ancien. Un dossier invalide est **listé** avec sa raison, pas
    masqué : c'est ce qui rend l'invariant « manifeste en dernier » observable plutôt que déclaratif."""
    root = snapshots_dir(settings)
    if not root.is_dir():
        return []
    out: list[dict] = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        out.append({"path": str(d), "name": d.name, **_read_manifest(d / MANIFEST)})
    return out


def _read_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {"valid": False, "reason": "manifeste absent — prise interrompue, instantané incomplet"}
    except ValueError:
        return {"valid": False, "reason": "manifeste illisible (JSON invalide)"}
    if data.get("schema") != SCHEMA:
        return {"valid": False, "reason": f"schéma {data.get('schema')!r} inconnu (attendu {SCHEMA})"}
    return {"valid": True, "created_at": data.get("created_at"), "forgemaster": data.get("forgemaster"),
            "entries": [e["name"] for e in data.get("entries", [])], "absent": data.get("absent", [])}


# --- utilitaires ------------------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --- CLI --------------------------------------------------------------------------------------------

def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `forgemaster snapshot <action>`."""
    if args.action == "create":
        dest = create(settings)
        print(f"instantané → {dest}")
        for line in _describe(dest / MANIFEST):
            print(f"  {line}")
        return 0
    if args.action == "restore":
        return _launch_restore(settings, args)
    snaps = list_snapshots(settings)
    if not snaps:
        print(f"aucun instantané sous {snapshots_dir(settings)}")
        return 0
    for snap in snaps:
        if not snap["valid"]:
            print(f"{snap['name']}  ⚠ INVALIDE — {snap['reason']}")
            continue
        print(f"{snap['name']}  {snap['created_at']}  forgemaster {snap['forgemaster']['version']} "
              f"({snap['forgemaster']['build_sha'] or 'build inconnu'})  [{', '.join(snap['entries'])}]")
    return 0


def _launch_restore(settings: Settings, args: argparse.Namespace) -> int:
    """LANCE `restore.py`, ne le réimplémente pas. On préfère la copie **figée dans l'instantané** : elle a
    été écrite avec son manifeste, donc elle le comprend — et le chemin de secours (lancer le script à la
    main) devient exactement celui qu'on exerce ici, au lieu d'un jumeau jamais joué."""
    ref = args.snapshot
    target = Path(ref).expanduser() if ("/" in ref or ref.startswith("~")) else snapshots_dir(settings) / ref
    if not target.is_dir():
        print(f"✗ instantané introuvable : {target}", file=sys.stderr)
        return 1
    script = target / RESTORE
    if not script.is_file():
        script = Path(__file__).with_name(RESTORE)   # instantané pris avant que le script y voyage
        print(f"⚠ {target.name} ne porte pas son {RESTORE} — on lance celui du forgemaster installé")
    cmd = [sys.executable, str(script), "--snapshot", str(target), "--home", str(settings.home)]
    if args.dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd, check=False).returncode  # noqa: S603 (argv construit ici, pas de shell)


def _describe(manifest: Path) -> list[str]:
    """Ce qui a été pris, ce qui manquait, ce qui reste dehors — dit à la prise, pas à la restauration."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    lines = [f"pris     : {e['name']} ({e['via']}, {e['sha256'][:12]}…)" for e in data["entries"]]
    if data["absent"]:
        lines.append(f"absent   : {', '.join(data['absent'])} (l'instance ne l'avait pas)")
    lines.append(f"hors périmètre : {', '.join(data['excluded'])}")
    return lines
