"""update — le verbe `forgemaster update apply` : poser un wheel LOCAL et revenir tout seul si ça ne sert pas.

Ce module est la **moitié locale et hors-ligne** d'un canal de mise à jour : il prend un `.whl` qu'on lui
désigne, rien d'autre. **Aucune** détection de version disponible, **aucun** manifeste servi, **aucune**
signature, **aucun** réseau. Le canal (proposer, consentir) reste hors périmètre — écrire une décision n'est
pas ouvrir un canal.

Le travail réel est fait par `apply_update.py`, script autonome (stdlib pure, zéro import `forgemaster`) que
ce
verbe **copie** puis **lance détaché**, sous le `python3` du système : une MAJ qui bascule le venv et
redémarre le service ne doit pas mourir parce que le shell qui l'a lancée a été fermé — ni, surtout, parce
que c'est le daemon lui-même qui l'a lancée et qu'on vient de l'arrêter. Le verbe, lui, **suit le journal**
et rend le code de sortie : détaché ne veut pas dire aveugle.

Ce module ne porte donc que ce qu'un script autonome ne peut pas porter : le **refus fail-closed**, avant
que quoi que ce soit ne bouge.

Quatre refus, tous explicites, jamais un devinage :

- **pas d'unité systemd** → la bascule exige un service gérable. On ne va pas inventer une façon de
  redémarrer le forgemaster de quelqu'un ;
- **une unité qui lance un venv EN DUR** → c'est l'état de toute installation antérieure au bleu/vert. Elle
  n'est pas cassée, elle est *non migrée* : le message dit la commande unique qui la migre. Réécrire l'unité
  sous les pieds de l'utilisateur serait pire que refuser ;
- **portée système sans être root** → `systemctl` échouerait au milieu, service arrêté. Refuser avant ;
- **du travail non commité dans `projects_root`** → cette racine n'entre PAS dans l'instantané. Le motif
  (« git fait autorité ») ne vaut que là où il *y a* une autorité : le refus la vérifie au lieu de la
  supposer. Seul le non commité bloque — « aucun remote » est un cas normal du produit distribué, et refuser
  dessus interdirait toute mise à jour à qui n'en veut pas.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.projects import authority as auth
from forgemaster.service import stable_link, unit_path

APPLY = "apply.py"
UPDATES = "updates"
FOLLOW_TIMEOUT = 900.0     # 15 min : venv neuf + pip install + deux redémarrages, large


class UpdateRefused(Exception):
    """Refus fail-closed. Levée AVANT tout effet — l'instance est intacte quand elle sort."""


def preflight(settings: Settings, *, wheel: str, unit: str | None, scope: str,
              authority: list[dict] | None = None) -> dict:
    """Vérifie tout ce qui doit l'être avant que la moindre chose bouge, et rend le plan (chemins + URL de
    sonde). Lève `UpdateRefused` avec ce qu'il faut faire — jamais un « impossible » nu.

    `authority` est le verdict par projet (`projects.authority.survey`), **calculé par l'appelant** et passé
    ici : ce module ne va pas chercher une connexion DB tout seul (injection explicite), et un preflight ne
    doit rien ouvrir en écriture avant d'avoir le droit de refuser."""
    whl = Path(wheel).expanduser()
    if not whl.is_file():
        raise UpdateRefused(f"wheel introuvable : {whl}")
    if whl.suffix != ".whl":
        raise UpdateRefused(f"{whl.name} n'est pas un wheel (.whl attendu) — aucun réseau, aucune "
                            f"résolution : ce verbe ne pose que le fichier qu'on lui désigne")
    socle = _preflight_service(settings, unit=unit, scope=scope)
    _refuse_uncommitted_work(authority or [], geste="cette MAJ")
    return {**socle, "wheel": whl, "authority": authority or []}


def _preflight_service(settings: Settings, *, unit: str | None, scope: str) -> dict:
    """Ce que l'aller ET le retour vérifient tous les deux : la portée, l'unité, le lien stable, et que
    l'unité passe **par** ce lien. Extrait de `preflight` le 2026-08-06 en écrivant `preflight_rollback` —
    dupliquer ces quatre refus aurait produit deux jeux de messages qui divergent, alors que c'est
    exactement le même invariant de déploiement qui est en jeu."""
    if scope == "system" and os.geteuid() != 0:
        raise UpdateRefused("portée système demandée sans être root — `systemctl` échouerait en plein "
                            "milieu, service arrêté. Relance en root, ou installe le service en portée "
                            "utilisateur (`forgemaster install-service`).")

    up = Path(unit).expanduser() if unit else unit_path(scope)
    if not up.is_file():
        raise UpdateRefused(f"aucune unité systemd à {up} — la bascule bleu/vert exige un service gérable. "
                            f"Installe-le : `forgemaster install-service`.")
    exec_bin, host, port = parse_exec_start(up.read_text(encoding="utf-8"))

    link = stable_link(settings)
    if not link.is_symlink():
        raise UpdateRefused(f"{link} n'est pas un lien stable vers le venv actif. Relance "
                            f"`forgemaster install-service` (il le pose), puis `systemctl daemon-reload`.")
    if not exec_bin.startswith(str(link) + os.sep):
        raise UpdateRefused(
            f"l'unité {up} lance {exec_bin} — un venv EN DUR. La bascule bleu/vert remplace un lien, pas "
            f"une installation : tant que l'unité ne passe pas par {link}, la MAJ n'aurait aucun effet sur "
            f"le service. Migre-la : `forgemaster install-service` puis `systemctl daemon-reload`.")

    return {"unit": up, "link": link, "venv": link.resolve(),
            "base_url": f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}",
            "scope": scope, "home": settings.home, "projects_root": settings.projects_root}


def _refuse_uncommitted_work(verdicts: list[dict], *, geste: str) -> None:
    """Le refus d'autorité : du travail que ni la MAJ ni le retour arrière ne protègent. `projects_root` est
    hors instantané — le motif « git fait autorité » n'est vrai que là où il Y A une autorité. On ne bloque
    QUE sur du non-commité : « aucun remote » est un cas normal du produit distribué, et refuser dessus
    interdirait toute MAJ.

    Porté par les deux gestes depuis le 2026-08-06 : revenir en arrière pendant qu'un travail non commité
    vit dans un worktree est exactement le geste à refuser — et c'était l'un des deux acquis que la phase 1a
    laissait sans câblage côté retour."""
    refused = auth.blocking(verdicts)
    if refused:
        details = "\n  ".join(f"{v['slug']} — {v['detail']}" for v in refused)
        raise UpdateRefused(
            f"{len(refused)} projet(s) portent du travail NON COMMITÉ, et `projects_root` n'entre pas dans "
            f"l'instantané : si {geste} tourne mal, ce travail-là ne reviendra pas.\n  {details}\n"
            f"  → commite (ou remise) dans chaque worktree cité, puis relance")


def preflight_rollback(settings: Settings, *, snapshot: str | None, unit: str | None, scope: str,
                       authority: list[dict] | None = None) -> dict:
    """Le préflight du retour **volontaire**. Même socle de service que l'aller, même refus d'autorité, plus
    la **résolution de la cible** : quel instantané remettre, et vers quel venv rebasculer.

    La correspondance instantané ↔ binaire se **dérive** (égalité de schéma), elle ne se stocke pas : aucun
    état nouveau, aucune ligne de plus au manifeste, et le garde vaut aussi pour les instantanés déjà pris.
    Sans référence, la cible par défaut est le plus récent instantané que la phase 1 marque `restaurable` —
    le seul état qui ramène binaire **et** données."""
    from forgemaster import snapshot as snap_mod

    socle = _preflight_service(settings, unit=unit, scope=scope)
    _refuse_uncommitted_work(authority or [], geste="ce retour arrière")

    snaps = snap_mod.list_snapshots(settings)
    if not snaps:
        raise UpdateRefused(
            f"aucun instantané sous {snap_mod.snapshots_dir(settings)} — il n'y a rien vers quoi revenir. "
            f"Un instantané est pris automatiquement avant chaque `forgemaster update apply`.")

    from forgemaster import restore

    actuel = socle["venv"].resolve()
    lu_courant = restore.python_schema(socle["venv"] / "bin" / "python")

    if snapshot:
        cible = next((s for s in snaps if s["name"] == snapshot or s["path"] == snapshot), None)
        if cible is None:
            connus = ", ".join(s["name"] for s in snaps[:5])
            raise UpdateRefused(f"instantané inconnu : {snapshot}. Les plus récents : {connus} "
                                f"(`forgemaster snapshot list` les classe avec leur état)")
        venv, refus = _cible_utilisable(settings, cible, actuel=actuel, lu_courant=lu_courant)
        if venv is None:
            raise UpdateRefused(f"{cible['name']} : {refus}\n{_PISTES}")
    else:
        # On PARCOURT, on ne prend pas le premier `restaurable` : le plus récent est souvent l'instantané de
        # sûreté d'un retour arrière déjà fait, qui ramène vers la version qu'on venait de quitter. Chercher
        # la première cible qui ramène VRAIMENT en arrière évite ce va-et-vient.
        venv, cible = None, None
        motifs: list[str] = []
        for info in snaps:
            venv, refus = _cible_utilisable(settings, info, actuel=actuel, lu_courant=lu_courant)
            if venv is not None:
                cible = info
                break
            motifs.append(f"{info['name']} — {refus}")
        if venv is None or cible is None:
            detail = "\n  ".join(motifs[:5])
            raise UpdateRefused(f"aucun instantané ne ramènerait en arrière :\n  {detail}\n{_PISTES}")

    return {**socle, "snapshot": Path(cible["path"]), "snapshot_name": cible["name"],
            "target_venv": venv, "authority": authority or []}


_PISTES = ("  → `forgemaster snapshot list` dit pour chacun ce qu'il ramènerait vraiment\n"
           "  → `forgemaster snapshot restore <instantané>` remet les DONNÉES seules, en le disant\n"
           "  → `forgemaster update apply --wheel <fichier>` est le verbe qui va, lui, en AVANT")


def _cible_utilisable(settings: Settings, info: dict, *, actuel: Path,
                      lu_courant: int | None) -> tuple[Path | None, str]:
    """`(venv cible, motif de refus)` — le motif est vide quand cette cible ramène **vraiment** en arrière.

    Quatre façons de ne pas en être une, et la quatrième s'est révélée en revue : après un retour arrière,
    l'instantané de sûreté est le plus récent et il est `restaurable` — le viser ferait repartir **en avant**
    vers la version qu'on vient de quitter. « Revenir » a un sens, et ce n'est pas « bouger »."""
    from forgemaster import restore

    if not info["valid"]:
        return None, f"instantané invalide — {info['reason']}"
    if info.get("state") != "restaurable":
        return None, (f"il est `{info.get('state', 'inconnu')}`, pas `restaurable` — "
                      f"{info.get('state_reason', 'état non mesuré')}")
    venv = _venv_pour(settings, Path(info["path"]))
    if venv is None:
        return None, ("il se dit `restaurable` mais son venv n'a pas été retrouvé — la liste et la "
                      "résolution ne voient pas le même disque, on ne devine pas")
    if venv.resolve() == actuel:
        return None, f"il correspond au venv DÉJÀ actif ({venv.name}) — il n'y a nulle part où revenir"
    lu_cible = restore.python_schema(venv / "bin" / "python")
    if lu_courant is not None and lu_cible is not None and lu_cible > lu_courant:
        return None, (f"son binaire lit le schéma {lu_cible} et le tien lit le {lu_courant} : ce serait "
                      f"aller EN AVANT, pas revenir")
    return venv, ""


def _venv_pour(settings: Settings, snapshot_dir: Path) -> Path | None:
    """Le venv dont le forgemaster lit **exactement** le schéma de cet instantané. Égalité, pas « au moins » :
    un binaire qui lit plus loin remettrait les données puis migrerait la base en avant — l'état que la
    phase 1 nomme `données seules`, et qui n'est pas un retour arrière."""
    from forgemaster import restore
    from forgemaster import snapshot as snap_mod

    manifest = json.loads((snapshot_dir / snap_mod.MANIFEST).read_text(encoding="utf-8"))
    voulu = restore.snapshot_schema(snapshot_dir, manifest)
    if voulu is None:
        return None
    root = settings.home / snap_mod.VENVS
    if not root.is_dir():
        return None
    for venv in sorted(root.iterdir(), reverse=True):
        if venv.is_dir() and restore.python_schema(venv / "bin" / "python") == voulu:
            return venv
    return None


def parse_exec_start(unit_text: str) -> tuple[str, str, int]:
    """Extrait `(binaire, host, port)` de l'`ExecStart` de l'unité. L'unité est la SEULE vérité sur le bind
    du service — le déduire de `forgemaster.env` ou d'un défaut sonderait une autre instance que celle qu'on
    vient de redémarrer, et conclurait au vert sur la mauvaise."""
    lines = [ln for ln in unit_text.splitlines() if ln.strip().startswith("ExecStart=")]
    if not lines:
        raise UpdateRefused("l'unité n'a pas d'`ExecStart=` — illisible, on ne devine pas")
    argv = shlex.split(lines[-1].split("=", 1)[1].strip().lstrip("-@+!"))
    host, port = "127.0.0.1", None
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "--host":
            host = value
        elif flag == "--port":
            port = value
    if port is None or not port.isdigit():
        raise UpdateRefused(f"impossible de lire le port dans `ExecStart={' '.join(argv)}` — sans lui, "
                            f"aucune vérification en vivant n'est possible, donc aucun retour arrière "
                            f"automatique. Réinstalle l'unité : `forgemaster install-service --port <n>`.")
    return argv[0], host, int(port)


def describe(plan: dict) -> list[str]:
    """Ce qui va se passer, dit avant de le faire (et seul contenu de `--dry-run`)."""
    lines = [
        f"wheel à poser   : {plan['wheel']}",
        f"venv actuel     : {plan['venv']}  (via {plan['link']})",
        f"unité systemd   : {plan['unit']}  (portée {plan['scope']})",
        f"sonde en vivant : {plan['base_url']}/health (readiness : 503 = elle dit pourquoi) puis "
        f"/api/version",
        "déroulé         : venv neuf à côté → sonde en isolation → arrêt + instantané à froid → "
        "bascule du lien → vérification en vivant → retour arrière automatique si elle échoue",
    ]
    # Ce qui n'a pas bloqué est DIT quand même : un projet sans remote n'est pas une faute, mais l'utilisateur
    # doit savoir que sa seule copie est ici et qu'elle n'entre pas dans l'instantané.
    noted = [v for v in plan.get("authority") or [] if v["state"] != "clean_pushed"]
    if noted:
        lines.append(f"hors instantané : {plan.get('projects_root', 'projects_root')} — "
                     f"{len(noted)} projet(s) à savoir")
        lines.extend(auth.describe(noted))
    return lines


def describe_rollback(plan: dict) -> list[str]:
    """Ce que le retour arrière va faire, dit avant de le faire (et seul contenu de `--dry-run`). Il nomme
    les DEUX gestes et leur ordre : c'est l'unité que la phase 3 rend exécutoire, et la dire ici est ce qui
    permet à quelqu'un de refuser en connaissance de cause."""
    lines = [
        f"instantané      : {plan['snapshot_name']}  ({plan['snapshot']})",
        f"venv cible      : {plan['target_venv']}  (le lien {plan['link']} y sera rebasculé)",
        f"venv actuel     : {plan['venv']}",
        f"unité systemd   : {plan['unit']}  (portée {plan['scope']})",
        f"sonde en vivant : {plan['base_url']}/health (readiness : 503 = elle dit pourquoi) puis "
        f"/api/version",
        "déroulé         : sonde du binaire cible en isolation → arrêt + instantané de SÛRETÉ à froid → "
        "bascule du lien PUIS restauration → vérification en vivant → retour du retour si elle échoue",
    ]
    noted = [v for v in plan.get("authority") or [] if v["state"] != "clean_pushed"]
    if noted:
        lines.append(f"hors instantané : {plan.get('projects_root', 'projects_root')} — "
                     f"{len(noted)} projet(s) à savoir")
        lines.extend(auth.describe(noted))
    return lines


def launch(settings: Settings, plan: dict, *, systemctl: str, service: str, detach: bool,
           mode: str = "apply") -> int:
    """Copie `apply.py` dans un dossier de run, le lance DÉTACHÉ sous le `python3` système, et suit son
    journal. Copié et non lancé depuis le paquet : le script doit survivre au venv qu'il remplace.

    **Un seul lanceur pour les deux gestes** : c'est le même applicateur hors-processus, le même dossier de
    run, le même journal et le même `result.json`. Ce que `mode` change tient dans les arguments de cible."""
    run_dir = settings.home / UPDATES / datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    script = run_dir / APPLY
    shutil.copyfile(Path(__file__).with_name("apply_update.py"), script)
    script.chmod(0o755)

    cmd = [_system_python(), str(script), "--mode", mode,
           "--home", str(plan["home"]), "--link", str(plan["link"]),
           "--run-dir", str(run_dir), "--base-url", plan["base_url"],
           "--service", service, "--systemctl", systemctl, "--scope", plan["scope"]]
    cmd += (["--wheel", str(plan["wheel"])] if mode == "apply" else
            ["--target-venv", str(plan["target_venv"]), "--snapshot", str(plan["snapshot"])])
    with (run_dir / "launch.log").open("w") as out:
        subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,  # noqa: S603
                         start_new_session=True, cwd=str(settings.home))
    quoi = "MAJ" if mode == "apply" else "retour arrière"
    print(f"{quoi} lancé(e) (détaché) — journal : {run_dir / 'journal.log'}")
    if detach:
        print("ça continue même si tu fermes ce terminal ; `cat` le journal pour le suivre.")
        return 0
    return follow(run_dir)


def follow(run_dir: Path, *, timeout: float = FOLLOW_TIMEOUT, poll: float = 0.3) -> int:
    """Suit `journal.log` jusqu'à ce que `result.json` apparaisse, et rend le code de sortie du script.
    Un suivi interrompu (délai) ne conclut PAS à l'échec : il dit où regarder. Le script, lui, continue."""
    journal, result = run_dir / "journal.log", run_dir / "result.json"
    seen = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if journal.exists():
            text = journal.read_text(encoding="utf-8", errors="replace")
            if len(text) > seen:
                print(text[seen:], end="", flush=True)
                seen = len(text)
        if result.is_file():
            data = json.loads(result.read_text(encoding="utf-8"))
            return int(data.get("rc", 2))
        time.sleep(poll)
    print(f"⚠ suivi interrompu après {timeout:.0f} s — la MAJ continue en arrière-plan. "
          f"Son verdict arrivera dans {result}.")
    return 2


def _system_python() -> str:
    """Le `python3` du SYSTÈME, jamais celui du venv qu'on est en train de remplacer."""
    return shutil.which("python3") or "/usr/bin/python3"


# --- CLI ----------------------------------------------------------------------------------------------

def _survey_authority(settings: Settings) -> list[dict]:
    """Le verdict d'autorité, ou une liste vide en dégradation honnête. On n'ouvre la base **que si elle
    existe déjà** : un preflight qui refuse ne doit pas avoir créé la base de son refus. Une base illisible
    ne bloque pas non plus — elle rend « je ne sais pas », et ce module ne bloque que sur ce qu'il SAIT."""
    if not settings.db_path.is_file():
        return []
    from forgemaster.db import store
    from forgemaster.git.internal import InternalGit
    try:
        conn = store.connect(settings.db_path)
    except sqlite3.Error:
        return []
    try:
        return auth.survey(conn, settings, InternalGit())
    except sqlite3.Error:
        return []                       # base d'un schéma qu'on ne lit pas : pas un motif de refus
    finally:
        conn.close()


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `forgemaster update <action>` — `apply` et `rollback` suivent la MÊME séquence : préflight qui
    refuse avant tout effet, description, puis lancement de l'applicateur détaché."""
    scope = "system" if getattr(args, "system", False) else "user"
    aller = args.action == "apply"
    quoi = "MAJ" if aller else "retour arrière"
    try:
        plan = (preflight(settings, wheel=args.wheel, unit=args.unit, scope=scope,
                          authority=_survey_authority(settings)) if aller else
                preflight_rollback(settings, snapshot=args.snapshot, unit=args.unit, scope=scope,
                                   authority=_survey_authority(settings)))
    except UpdateRefused as exc:
        print(f"✗ {quoi} refusé(e) — rien n'a été touché.\n  {exc}", file=sys.stderr)
        return 1
    for line in (describe(plan) if aller else describe_rollback(plan)):
        print(line)
    if args.dry_run:
        print("\n(--dry-run : rien n'a été lancé)")
        return 0
    print()
    return launch(settings, plan, systemctl=args.systemctl, service=args.service, detach=args.detach,
                  mode="apply" if aller else "rollback")
