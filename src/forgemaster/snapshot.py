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

from forgemaster.apply_update import KEEP_SNAPSHOTS
from forgemaster.config import Settings
from forgemaster.restore import python_schema, snapshot_schema

SCHEMA = 1                 # un `restore.py` qui lit un schéma inconnu REFUSE honnêtement au lieu de deviner
# Dérivée de LA politique (`apply_update.ROLLBACK_DEPTH`), pas posée à côté d'elle : les deux rétentions —
# venvs et instantanés — répondent à la même question, « jusqu'où sait-on revenir ? ». La marge de 2 tient
# le scénario « la MAJ a échoué, j'ai restauré, j'ai retenté, ça a re-échoué ». La valeur ne change pas (3) ;
# ce qui change est qu'elle ne peut plus dériver toute seule de celle des venvs. Déclarée chez le module
# stdlib-pur depuis le 2026-08-06 (verbe `rollback`) : lui aussi doit compter les crans, pour refuser une
# cible que sa propre prise de sûreté détruirait — et il ne peut rien importer d'ici.
KEEP = KEEP_SNAPSHOTS
MANIFEST = "manifest.json"
VENVS = "venvs"            # même convention que `apply_update._parse` (défaut `<home>/venvs`)
MARQUEURS = {"restaurable": "✔", "données seules": "⚠", "irrestaurable": "✗", "inconnu": "·"}
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
    masqué : c'est ce qui rend l'invariant « manifeste en dernier » observable plutôt que déclaratif.

    Chaque instantané valide porte en plus son **état de restauration** (`state` + `state_reason`) : tous ne
    se valent pas, et les présenter côte à côte sans le dire laisse choisir celui qui ne ramènera pas. La
    mesure coûte **une sonde par venv candidat DISTINCT** (2 en régime, 3 au premier saut) plus **une lecture
    JSON par run de MAJ** — et le nombre de runs, lui, n'est borné par rien aujourd'hui (`<home>/updates` n'a
    pas de rétention, contrairement aux venvs et aux instantanés). Dit ici plutôt que découvert : le coût est
    linéaire en runs, pas en instantanés."""
    root = snapshots_dir(settings)
    if not root.is_dir():
        return []
    venvs = venv_schemas(settings)
    out: list[dict] = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        info, raw = _read_manifest(d / MANIFEST)
        if raw is not None:
            info |= _restorability(d, raw, venvs)
        out.append({"path": str(d), "name": d.name, **info})
    return out


def _read_manifest(path: Path) -> tuple[dict, dict | None]:
    """Rend `(ce qu'on expose, le manifeste brut)`. Le brut n'est **pas** exposé — il ne sert qu'à
    `_restorability`, qui a besoin des entrées complètes là où l'appelant n'a besoin que de leurs noms."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {"valid": False, "reason": "manifeste absent — prise interrompue, instantané incomplet"}, None
    except ValueError:
        return {"valid": False, "reason": "manifeste illisible (JSON invalide)"}, None
    if data.get("schema") != SCHEMA:
        return {"valid": False, "reason": f"schéma {data.get('schema')!r} inconnu (attendu {SCHEMA})"}, None
    return ({"valid": True, "created_at": data.get("created_at"), "forgemaster": data.get("forgemaster"),
             "entries": [e["name"] for e in data.get("entries", [])], "absent": data.get("absent", [])},
            data)


def venv_schemas(settings: Settings) -> dict[Path, int]:
    """Le schéma que sait lire **chaque** venv vers lequel un retour arrière peut basculer — donc la seule
    mesure qui décide de l'état d'un instantané. Ordonné par **préférence** (cf. `_venvs_candidats`), et
    l'ordre EST le contrat : `_premier_du_schema` s'en sert pour choisir.

    Un venv qu'on ne sait pas sonder est **absent** du résultat plutôt que compté à zéro : un schéma
    indéterminable n'est pas un schéma bas (même raison que `restore._user_version`).

    Clé = `Path` et non plus le nom : deux venvs de racines différentes peuvent porter le même nom (le venv
    d'origine s'appelle typiquement `forgemaster`), et une collision de clés en effacerait un en silence."""
    found: dict[Path, int] = {}
    for venv in _venvs_candidats(settings):
        readable = python_schema(venv / "bin" / "python")
        if readable is not None:
            found[venv] = readable
    return found


def _venvs_candidats(settings: Settings) -> list[Path]:
    """Les venvs posés, du **plus préférable au moins** : les estampillés de `<home>/venvs` du plus récent au
    plus ancien, puis ceux qu'un run de MAJ a nommés et qui vivent **ailleurs**.

    Ce « ailleurs » est tout l'objet de cette fonction. `<home>/venvs` est **créé par le premier `update
    apply`** : le venv d'origine — celui que `pip install` a posé (`~/.venvs/forgemaster`) et que le lien
    stable désignait avant la toute première bascule — n'y est JAMAIS. Ne regarder que ce dossier rendait
    donc le **premier** saut sans retour, alors que le binaire d'avant était intact sur le disque (mesuré
    le 2026-08-06 sur vrai systemd).

    On le **dérive**, on ne le stocke pas : `updates/<stamp>/result.json` porte déjà `venv_avant` depuis le
    2026-08-06. Aucun état nouveau, aucune ligne de plus au manifeste, `SCHEMA` inchangé — et ça vaut pour
    les instantanés **déjà pris**, ce qu'un marqueur posé au prochain `apply` ne ferait pas. Même doctrine
    que `update.preflight_rollback` : la correspondance instantané ↔ binaire se dérive.

    Ce venv-là n'est jamais **purgé** non plus (`apply_update._purge_venvs` ne balaie que `<home>/venvs`), et
    c'est voulu : il appartient à l'utilisateur, pas au cycle de MAJ. La rétention ne s'applique qu'à ce que
    le produit a créé."""
    root = settings.home / VENVS
    estampilles = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True) if root.is_dir() else []
    vus = {p.resolve() for p in estampilles}
    ailleurs: list[Path] = []
    for avant in _venvs_nommes_par_les_runs(settings):
        if not avant.is_dir():          # purgé, ou l'utilisateur l'a effacé : on ne devine pas, on l'ignore
            continue
        resolu = avant.resolve()
        if resolu in vus:               # run N ≥ 2 : `venv_avant` est un estampillé, déjà dans la liste
            continue
        vus.add(resolu)
        ailleurs.append(avant)
    return estampilles + ailleurs


def _venvs_nommes_par_les_runs(settings: Settings) -> list[Path]:
    """Les `venv_avant` écrits par les runs de MAJ, du **plus récent au plus ancien**. Un run sans verdict
    (interrompu), au JSON illisible, ou d'une version antérieure à cette clé, est **sauté** — pas une erreur :
    ces dossiers sont des journaux, et un journal incomplet n'invalide pas les autres."""
    from forgemaster.update import UPDATES  # tardif, comme `update` importe `snapshot` : pas de cycle

    root = settings.home / UPDATES
    if not root.is_dir():
        return []
    out: list[Path] = []
    for run in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        try:
            data = json.loads((run / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        avant = data.get("venv_avant") if isinstance(data, dict) else None
        if avant:
            out.append(Path(str(avant)))
    return out


def _premier_du_schema(venvs: dict[Path, int], voulu: int) -> Path | None:
    """Le venv qui lit **exactement** ce schéma, pris dans l'ordre de préférence de `venv_schemas`.

    **Une seule** règle de choix, appelée par les deux marches — l'état (`_restorability`) et la résolution
    (`update._venv_pour`). Elles parcouraient chacune `<home>/venvs` de leur côté : deux marches qui
    choisissent séparément finissent par ne pas choisir pareil, et `update._cible_utilisable` porte déjà le
    refus que ça produit (« la liste et la résolution ne voient pas le même disque »)."""
    return next((v for v, s in venvs.items() if s == voulu), None)


def venv_for_schema(settings: Settings, voulu: int) -> Path | None:
    """`_premier_du_schema` depuis les réglages — la porte d'entrée de `update._venv_pour`."""
    return _premier_du_schema(venv_schemas(settings), voulu)


def _restorability(snapshot_dir: Path, manifest: dict, venvs: dict[Path, int]) -> dict:
    """Les **trois états** d'un instantané, face aux binaires réellement disponibles. Ils reprennent le
    vocabulaire du garde de `restore.check_compatibility` — deux formulations du même invariant seraient
    deux façons de le comprendre :

    - `restaurable` — un venv porte **exactement** le schéma de l'instantané : on remet le binaire ET les
      données, et on est revenu. C'est le seul état qui tient la promesse du retour arrière.
    - `données seules` — aucun venv n'a ce schéma, mais au moins un le dépasse : la remise passera le garde,
      puis la base **migrera en avant** à la première ouverture. On récupère ses données ; on ne revient pas,
      et on ne pourra plus. C'est le piège que cette phase existe pour rendre visible.
    - `irrestaurable` — tous les binaires disponibles lisent moins loin : `restore` refusera, et il a raison.

    Rien à mesurer (aucun venv sondable) → `inconnu`, pas un état. Une instance installée par `pip` et jamais
    mise à jour n'a ni `<home>/venvs` ni run de MAJ : rendre `irrestaurable` sur ce qui est NORMAL ferait un
    check défaillant, donc ignoré.
    """
    entries = manifest.get("entries")
    # `_read_manifest` tolère un manifeste tronqué (`data.get("entries", [])`) et le rend `valid` ;
    # `snapshot_schema`, lui, fait `manifest["entries"]`. Sans cette garde, `snapshot list` — le verbe
    # qu'on lance PRÉCISÉMENT quand ça va mal — remonte un `KeyError` au lieu de lister. Trouvé en revue
    # du diff, après avoir vérifié que le cas se produit (manifeste `{"schema": 1}` seul).
    if not isinstance(entries, list):
        return {"state": "inconnu",
                "state_reason": "manifeste tronqué : il ne déclare aucune entrée, le schéma de la base ne "
                                "s'y lit pas"}
    snap = snapshot_schema(snapshot_dir, manifest)
    if snap is None:
        return {"state": "restaurable",
                "state_reason": "aucune base dans cet instantané — rien qui puisse devenir illisible"}
    if not venvs:
        return {"state": "inconnu",
                "state_reason": f"aucun venv sondable — ni sous <home>/{VENVS}, ni parmi ceux qu'un run de "
                                f"MAJ a nommés : cette instance n'a pas été posée par le cycle de MAJ, "
                                f"l'état ne se mesure pas ici"}
    exact = _premier_du_schema(venvs, snap)
    if exact is not None:
        # Le CHEMIN, pas le nom : le venv d'origine s'appelle `forgemaster`, ce qui ne dit pas où il est —
        # et ce motif est exactement ce qu'on relit quand un retour arrière se discute.
        return {"state": "restaurable",
                "state_reason": f"le venv {exact} porte le schéma {snap} : binaire et données reviennent "
                                f"ensemble"}
    au_dessus = [s for s in venvs.values() if s > snap]
    if au_dessus:
        proche = min(au_dessus)
        return {"state": "données seules",
                "state_reason": f"aucun venv ne porte le schéma {snap} ; le plus proche lit le {proche}. "
                                f"La remise passera, puis la base migrera EN AVANT vers le {proche} — tu "
                                f"récupères tes données, tu ne reviens pas, et la base monte en forward-only"}
    return {"state": "irrestaurable",
            "state_reason": f"cet instantané porte le schéma {snap} et aucun venv posé ne lit plus loin que "
                            f"le {max(venvs.values())} — `restore` refusera, il a raison"}


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
            print(f"⚠ {snap['name']}  INVALIDE — {snap['reason']}")
            continue
        etat = snap.get("state", "inconnu")
        print(f"{MARQUEURS.get(etat, '·')} {snap['name']}  {snap['created_at']}  "
              f"forgemaster {snap['forgemaster']['version']} "
              f"({snap['forgemaster']['build_sha'] or 'build inconnu'})  [{', '.join(snap['entries'])}]")
        # Les deux états qui trompent se DISENT, ligne à ligne. `restaurable` n'a rien à ajouter, et
        # `inconnu` se dit une fois en pied de liste : le répéter noierait ceux qui comptent.
        if etat in ("données seules", "irrestaurable"):
            print(f"    → {etat.upper()} — {snap['state_reason']}")
    if any(s.get("state") == "inconnu" for s in snaps):
        print(f"\n· état de restauration non mesuré : aucun venv sondable, ni sous <home>/{VENVS} ni parmi "
              f"ceux qu'un run de MAJ a nommés — cette instance n'a pas été posée par le cycle de MAJ")
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
    # Ajouté SEULEMENT s'il est demandé : le script qu'on lance est de préférence la copie FIGÉE dans
    # l'instantané, et une copie d'avant ce drapeau ferait sortir argparse en usage. Le passer
    # inconditionnellement casserait la restauration des instantanés anciens — exactement ceux qu'on
    # restaure le jour où ça compte.
    if getattr(args, "allow_unverified_binary", False):
        cmd.append("--allow-unverified-binary")
    return subprocess.run(cmd, check=False).returncode  # noqa: S603 (argv construit ici, pas de shell)


def _describe(manifest: Path) -> list[str]:
    """Ce qui a été pris, ce qui manquait, ce qui reste dehors — dit à la prise, pas à la restauration."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    lines = [f"pris     : {e['name']} ({e['via']}, {e['sha256'][:12]}…)" for e in data["entries"]]
    if data["absent"]:
        lines.append(f"absent   : {', '.join(data['absent'])} (l'instance ne l'avait pas)")
    lines.append(f"hors périmètre : {', '.join(data['excluded'])}")
    return lines
