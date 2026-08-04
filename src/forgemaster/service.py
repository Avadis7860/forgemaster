"""service — génération/installation d'une unité systemd pour `forgemaster serve` (self-hosted « production
»).

Sobre : on RÉUTILISE le seam d'environnement (`FORGEMASTER_HOME/forgemaster.env`) plutôt qu'un daemon-manager
maison.
Deux portées : `user` (défaut, **sans root**, `~/.config/systemd/user/`) et `system` (root,
`/etc/systemd/system/`). `render_unit` est **pur** (testable) ; `install_service` écrit l'unité + un
`forgemaster.env` gabarit et **imprime** les commandes `systemctl` à lancer — on n'exécute pas systemctl à ta
place (pas de footgun privilège, pas d'effet caché).
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from forgemaster.config import Settings


def set_env_keys(env_path: Path, updates: dict[str, str]) -> None:
    """Pose/écrase des clés `CLÉ=valeur` dans un `forgemaster.env` (EnvironmentFile systemd) de façon
    **idempotente** et **atomique** (tmp + `os.replace`, 0600). Préserve les autres lignes (commentaires
    inclus) ; ajoute en fin les clés neuves. Crée le fichier (en-tête minimal) s'il manque. Les valeurs sont
    des **références opaques** ou des réglages non-secrets — jamais un secret en clair (cf. `mcp wire`)."""
    env_path = Path(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if ("=" in line and not line.lstrip().startswith("#")) else None
        out.append(f"{key}={remaining.pop(key)}" if key in remaining else line)
    out.extend(f"{key}={val}" for key, val in remaining.items())   # clés neuves → en fin
    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = env_path.with_name(env_path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, env_path)


def load_env_file(env_path: Path) -> dict[str, str]:
    """Charge les paires `CLÉ=valeur` d'un `forgemaster.env` (EnvironmentFile) dans `os.environ` — la CLI
    obtient ainsi la même config que le service systemd (qui, lui, lit l'`EnvironmentFile`). **Non-override**
    : une clé déjà présente dans l'environnement réel gagne (comme `Environment=` prime sur `EnvironmentFile=`
    sous systemd). Ignore lignes vides et commentaires ; parse symétrique de `set_env_keys`. No-op si le
    fichier est absent. Retourne les clés effectivement posées (diagnostic/test). Sans cette parité, un
    `forgemaster dispatch` en CLI perdait le câblage MCP **en silence** (seul le service lisait
    forgemaster.env)."""
    env_path = Path(env_path)
    if not env_path.exists():
        return {}
    loaded: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:
            os.environ[key] = val
            loaded[key] = val
    return loaded


def _forgemaster_bin() -> str:
    """Chemin du binaire `forgemaster` (venv courant si présent, sinon le nom nu résolu par le PATH)."""
    cand = Path(sys.executable).with_name("forgemaster")
    return str(cand) if cand.exists() else "forgemaster"


LINK_NAME = "current"


def stable_link(settings: Settings) -> Path:
    """`<home>/current` — le lien symbolique vers le venv ACTIF, et le seul chemin que l'unité systemd
    connaisse. Un lien plutôt qu'un venv en dur parce que c'est ce qui rend une MAJ **réversible** : poser
    une nouvelle version, c'est remplacer un lien ; revenir en arrière, c'est le remplacer dans l'autre
    sens. Sans lui, `forgemaster update apply` n'a aucun moyen de défaire ce qu'il a fait — il refuse donc."""
    return settings.home / LINK_NAME


def pose_stable_link(settings: Settings) -> Path | None:
    """Pose/rafraîchit `<home>/current` vers le venv qui exécute ce code, de façon ATOMIQUE (`symlink` sur
    un temporaire + `os.replace`) : jamais d'instant où l'unité pointerait le vide. Rend `None` quand on ne
    tourne pas dans un venv qui porte la commande `forgemaster` (checkout `python -m forgemaster`, install
    système)
    — cas où l'unité retombe sur le binaire résolu par le PATH, et où `update apply` refusera honnêtement
    plutôt que de basculer un lien qui n'existe pas."""
    venv = Path(sys.prefix)
    if not (venv / "bin" / "forgemaster").exists():
        return None
    link = stable_link(settings)
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(link.name + ".tmp")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(venv, tmp)
    os.replace(tmp, link)
    return link


def render_unit(settings: Settings, *, host: str, port: int, scope: str = "user",
                exec_bin: str | None = None) -> str:
    """Rend le contenu d'une unité systemd pour `forgemaster serve`. `Environment=HOME` est **obligatoire** :
    sans lui, git ne lit pas le helper de credentials → fetch/push non authentifiés **en silence** (cf.
    systemd-git-service-needs-home). L'`EnvironmentFile=-…` (optionnel, préfixe `-`) porte FORGEMASTER_HOME,
    le backend de coffre, etc. En portée `system` on épingle `User=`/`Group=` à l'utilisateur courant."""
    if scope not in ("user", "system"):
        raise ValueError(f"scope inconnu : {scope!r} (attendu 'user' ou 'system')")
    home = settings.home
    env_file = home / "forgemaster.env"
    identity = "" if scope == "user" else f"User={getpass.getuser()}\nGroup={getpass.getuser()}\n"
    wanted_by = "default.target" if scope == "user" else "multi-user.target"
    return (
        "[Unit]\n"
        "Description=forgemaster — forge/orchestrateur local (daemon FastAPI + web)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"{identity}"
        f"Environment=HOME={Path.home()}\n"
        f"Environment=FORGEMASTER_HOME={home}\n"
        f"EnvironmentFile=-{env_file}\n"
        f"ExecStart={exec_bin or _forgemaster_bin()} serve --host {host} --port {port}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "\n"
        "[Install]\n"
        f"WantedBy={wanted_by}\n"
    )


def _env_template(settings: Settings, *, host: str, port: int) -> str:
    """Gabarit `forgemaster.env` (EnvironmentFile) : tout est commenté sauf le défaut sobre. L'utilisateur
    décommente pour passer à BWS, changer le bind, etc. Aucun secret ici — le token vit dans le coffre."""
    return (
        "# forgemaster.env — réglages du service (EnvironmentFile). Aucun secret ici.\n"
        f"FORGEMASTER_HOME={settings.home}\n"
        "# Coffre de secrets : 'file' (défaut, chiffré local, zéro-config) ou 'bws' (Bitwarden SM).\n"
        "# FORGEMASTER_SECRET_STORE=bws\n"
        "# BWS_ACCESS_TOKEN_FILE=/chemin/vers/le/token   # requis si FORGEMASTER_SECRET_STORE=bws\n"
        f"# Bind du daemon (repris par le service) : host={host} port={port}\n"
    )


def _unit_dir(scope: str) -> Path:
    return (Path.home() / ".config/systemd/user") if scope == "user" else Path("/etc/systemd/system")


def unit_path(scope: str = "user") -> Path:
    """Où vit l'unité de CE forgemaster, par portée — la même vérité pour `install_service` (qui l'écrit) et
    pour `update.preflight` (qui la lit)."""
    return _unit_dir(scope) / "forgemaster.service"


def install_service(
    settings: Settings, *, host: str, port: int, scope: str = "user",
) -> tuple[Path, Path, str]:
    """Écrit l'unité systemd + un `forgemaster.env` gabarit (jamais écrasé s'il existe). Retourne
    `(unit_path, env_path, systemctl_hint)` — l'appelant imprime le hint, on n'exécute pas systemctl.

    Pose au passage le **lien stable** `<home>/current` et fait pointer l'unité DESSUS : c'est ce qui rend
    une MAJ réversible (cf. `pose_stable_link`). Relancer cette commande est donc aussi la **migration**
    d'une installation antérieure — un `install-service` + `systemctl daemon-reload`, rien d'autre."""
    settings.home.mkdir(parents=True, exist_ok=True)
    link = pose_stable_link(settings)
    exec_bin = str(link / "bin" / "forgemaster") if link else None

    unit_file = unit_path(scope)
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    unit_file.write_text(render_unit(settings, host=host, port=port, scope=scope, exec_bin=exec_bin),
                         encoding="utf-8")

    env_path = settings.home / "forgemaster.env"
    if not env_path.exists():                            # ne jamais écraser une conf existante
        env_path.write_text(_env_template(settings, host=host, port=port), encoding="utf-8")

    flag = "--user " if scope == "user" else ""
    sudo = "" if scope == "user" else "sudo "
    hint = (f"{sudo}systemctl {flag}daemon-reload && "
            f"{sudo}systemctl {flag}enable --now forgemaster")
    return unit_file, env_path, hint
