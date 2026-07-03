"""service — génération/installation d'une unité systemd pour `cockpit serve` (self-hosted « production »).

Sobre : on RÉUTILISE le seam d'environnement (`COCKPIT_HOME/cockpit.env`) plutôt qu'un daemon-manager maison.
Deux portées : `user` (défaut, **sans root**, `~/.config/systemd/user/`) et `system` (root,
`/etc/systemd/system/`). `render_unit` est **pur** (testable) ; `install_service` écrit l'unité + un
`cockpit.env` gabarit et **imprime** les commandes `systemctl` à lancer — on n'exécute pas systemctl à ta
place (pas de footgun privilège, pas d'effet caché).
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

from cockpit.config import Settings


def _cockpit_bin() -> str:
    """Chemin du binaire `cockpit` (venv courant si présent, sinon le nom nu résolu par le PATH)."""
    cand = Path(sys.executable).with_name("cockpit")
    return str(cand) if cand.exists() else "cockpit"


def render_unit(settings: Settings, *, host: str, port: int, scope: str = "user") -> str:
    """Rend le contenu d'une unité systemd pour `cockpit serve`. `Environment=HOME` est **obligatoire** :
    sans lui, git ne lit pas le helper de credentials → fetch/push non authentifiés **en silence** (cf.
    systemd-git-service-needs-home). L'`EnvironmentFile=-…` (optionnel, préfixe `-`) porte COCKPIT_HOME,
    le backend de coffre, etc. En portée `system` on épingle `User=`/`Group=` à l'utilisateur courant."""
    if scope not in ("user", "system"):
        raise ValueError(f"scope inconnu : {scope!r} (attendu 'user' ou 'system')")
    home = settings.home
    env_file = home / "cockpit.env"
    identity = "" if scope == "user" else f"User={getpass.getuser()}\nGroup={getpass.getuser()}\n"
    wanted_by = "default.target" if scope == "user" else "multi-user.target"
    return (
        "[Unit]\n"
        "Description=cockpit — forge/orchestrateur local (daemon FastAPI + web)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"{identity}"
        f"Environment=HOME={Path.home()}\n"
        f"Environment=COCKPIT_HOME={home}\n"
        f"EnvironmentFile=-{env_file}\n"
        f"ExecStart={_cockpit_bin()} serve --host {host} --port {port}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "\n"
        "[Install]\n"
        f"WantedBy={wanted_by}\n"
    )


def _env_template(settings: Settings, *, host: str, port: int) -> str:
    """Gabarit `cockpit.env` (EnvironmentFile) : tout est commenté sauf le défaut sobre. L'utilisateur
    décommente pour passer à BWS, changer le bind, etc. Aucun secret ici — le token vit dans le coffre."""
    return (
        "# cockpit.env — réglages du service (EnvironmentFile). Aucun secret ici.\n"
        f"COCKPIT_HOME={settings.home}\n"
        "# Coffre de secrets : 'file' (défaut, chiffré local, zéro-config) ou 'bws' (Bitwarden SM).\n"
        "# COCKPIT_SECRET_STORE=bws\n"
        "# BWS_ACCESS_TOKEN_FILE=/chemin/vers/le/token   # requis si COCKPIT_SECRET_STORE=bws\n"
        f"# Bind du daemon (repris par le service) : host={host} port={port}\n"
    )


def _unit_dir(scope: str) -> Path:
    return (Path.home() / ".config/systemd/user") if scope == "user" else Path("/etc/systemd/system")


def install_service(
    settings: Settings, *, host: str, port: int, scope: str = "user",
) -> tuple[Path, Path, str]:
    """Écrit l'unité systemd + un `cockpit.env` gabarit (jamais écrasé s'il existe). Retourne
    `(unit_path, env_path, systemctl_hint)` — l'appelant imprime le hint, on n'exécute pas systemctl."""
    unit_dir = _unit_dir(scope)
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "cockpit.service"
    unit_path.write_text(render_unit(settings, host=host, port=port, scope=scope), encoding="utf-8")

    settings.home.mkdir(parents=True, exist_ok=True)
    env_path = settings.home / "cockpit.env"
    if not env_path.exists():                            # ne jamais écraser une conf existante
        env_path.write_text(_env_template(settings, host=host, port=port), encoding="utf-8")

    flag = "--user " if scope == "user" else ""
    sudo = "" if scope == "user" else "sudo "
    hint = (f"{sudo}systemctl {flag}daemon-reload && "
            f"{sudo}systemctl {flag}enable --now cockpit")
    return unit_path, env_path, hint
