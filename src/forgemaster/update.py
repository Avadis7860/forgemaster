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

    # Quatrième refus : du travail que la MAJ ne protège pas. `projects_root` est hors instantané — le motif
    # « git fait autorité » n'est vrai que là où il Y A une autorité. On ne bloque QUE sur du non-commité :
    # « aucun remote » est un cas normal du produit distribué, et refuser dessus interdirait toute MAJ.
    verdicts = authority or []
    refused = [v for v in verdicts if v["state"] == auth.BLOCKING]
    if refused:
        details = "\n  ".join(f"{v['slug']} — {v['detail']}" for v in refused)
        raise UpdateRefused(
            f"{len(refused)} projet(s) portent du travail NON COMMITÉ, et `projects_root` n'entre pas dans "
            f"l'instantané : si cette MAJ tourne mal, ce travail-là ne reviendra pas.\n  {details}\n"
            f"  → commite (ou remise) dans chaque worktree cité, puis relance")

    return {"wheel": whl, "unit": up, "link": link, "venv": link.resolve(),
            "base_url": f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}",
            "scope": scope, "home": settings.home, "authority": verdicts,
            "projects_root": settings.projects_root}


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
        f"sonde en vivant : {plan['base_url']}/health puis /api/version",
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


def launch(settings: Settings, plan: dict, *, systemctl: str, service: str, detach: bool) -> int:
    """Copie `apply.py` dans un dossier de run, le lance DÉTACHÉ sous le `python3` système, et suit son
    journal. Copié et non lancé depuis le paquet : le script doit survivre au venv qu'il remplace."""
    run_dir = settings.home / UPDATES / datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    script = run_dir / APPLY
    shutil.copyfile(Path(__file__).with_name("apply_update.py"), script)
    script.chmod(0o755)

    cmd = [_system_python(), str(script),
           "--wheel", str(plan["wheel"]), "--home", str(plan["home"]), "--link", str(plan["link"]),
           "--run-dir", str(run_dir), "--base-url", plan["base_url"],
           "--service", service, "--systemctl", systemctl, "--scope", plan["scope"]]
    with (run_dir / "launch.log").open("w") as out:
        subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,  # noqa: S603
                         start_new_session=True, cwd=str(settings.home))
    print(f"MAJ lancée (détachée) — journal : {run_dir / 'journal.log'}")
    if detach:
        print("elle continue même si tu fermes ce terminal ; `cat` le journal pour la suivre.")
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
    """Route `forgemaster update <action>`."""
    scope = "system" if getattr(args, "system", False) else "user"
    try:
        plan = preflight(settings, wheel=args.wheel, unit=args.unit, scope=scope,
                         authority=_survey_authority(settings))
    except UpdateRefused as exc:
        print(f"✗ MAJ refusée — rien n'a été touché.\n  {exc}", file=sys.stderr)
        return 1
    for line in describe(plan):
        print(line)
    if args.dry_run:
        print("\n(--dry-run : rien n'a été lancé)")
        return 0
    print()
    return launch(settings, plan, systemctl=args.systemctl, service=args.service, detach=args.detach)
