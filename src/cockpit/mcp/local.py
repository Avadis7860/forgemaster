"""mcp.local — l'instance forgemaster-catalogs que CE cockpit fait tourner (topologie co-installée).

Pendant hôte-niveau de `provision.mcp` : celui-là **câble** un endpoint dans le `.mcp.json` d'un worker,
celui-ci **fait tourner le serveur** sur la même machine. La décision d'édition du 2026-08-02 (§4) déclare
deux topologies — `co-installed` et `remote` — et exige que l'instance **dise laquelle elle est** ; jusqu'ici
seule la seconde existait (`COCKPIT_MCP_ENDPOINT` vers un serveur d'ailleurs).

Calqué sur `cockpit.tools` (l'outillage hôte-niveau), volontairement et jusque dans le détail :
**seams purs** (chemins, argv, rendu de fichiers) séparés de l'**exécution** (runner injecté) · lecture de
provenance **locale, zéro réseau, qui ne lève jamais** · clone **anonyme par défaut** (aucun credential ne
peut se glisser dans l'env d'un enfant git). Ce n'est pas une ressemblance de style : c'est le même problème.

**Ce qu'on installe est un LECTEUR, pas un corpus.** `deploy/README.md` du serveur le dit — le déploiement
pose un lecteur, pas un pipeline. Le `DATA_ROOT` est de la donnée de l'**opérateur**, fournie explicitement :
sans elle on REFUSE, plutôt que de démarrer un serveur qui rendrait `200` sur un corpus fantôme (jamais de
cap silencieux). Le cockpit ne clone aucun corpus, jamais : le nôtre ne se distribue pas, et mettre le
produit dans le métier de distribuer de la donnée le mettrait du mauvais côté de cette frontière.

**La topologie est DÉDUITE du disque, jamais déclarée.** Une clé d'env `…_TOPOLOGY` serait un champ qui peut
mentir : rien ne le re-vérifie après un re-câblage. Ici, deux faits lisibles localement suffisent — le
serveur est-il installé sous `$COCKPIT_HOME/mcp/venv` ? l'endpoint consommé est-il en loopback ? — et leur
conjonction EST la topologie.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
from collections.abc import Mapping
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from cockpit.config import Settings
from cockpit.core.run import RunResult, run
from cockpit.provision import mcp as wiring
from cockpit.tools import Runner, anonymous_env, dist_provenance, run_step, venv_site_packages

# Le serveur, ÉPINGLÉ. §3 de la décision d'édition : une pièce de classe « nous » monte AVEC l'édition,
# jamais seule — l'inverse exact de `tools.MAP_REF = "main"` (réf mobile, dont la mobilité est précisément
# ce qui a rendu `cockpit tools check` nécessaire). Bump = une entrée CHANGELOG + une édition.
SERVER_REPO = "https://github.com/Avadis7860/forgemaster-catalogs.git"
SERVER_REF = "0d481d3c2795a35549515b67acb3abd6b314e31b"
SERVER_DIST = "forgemaster-catalogs"          # nom de DISTRIBUTION (pip), pas de module
SERVER_UNIT = "forgemaster-catalogs"          # nom d'unité systemd — celui du dépôt amont, pas un alias

DEFAULT_PORT = 8080                           # le défaut du serveur lui-même (cockpit, lui, sert sur 8700)
LOOPBACK_HOST = "127.0.0.1"
# Contrat JWT partagé avec le serveur. Réexporté depuis `provision.mcp` plutôt que redéclaré : deux
# constantes jumelles qui dérivent l'une de l'autre produiraient un serveur qui refuse les jetons de son
# propre cockpit, et le symptôme (401) ne nommerait pas la cause.
JWT_ISSUER = wiring.MCP_ISSUER
JWT_AUDIENCE = wiring.MCP_AUDIENCE

_SECRET_BYTES = 48                            # ≫ 32 caractères, le plancher HS256 exigé par `wire()`
_STEP_TIMEOUT_S = 900                         # clone git + build : lent mais borné (même ordre que tools)


class McpInstallError(RuntimeError):
    """Co-install impossible : racine de donnée absente, étape pip rouge, coffre incompatible. Message
    humain (`str(exc)`) réutilisable tel quel par la CLI."""


# -- seams PURS (chemins, URL, argv, rendu de fichiers — zéro subprocess) ----------------------------

def mcp_root(settings: Settings) -> Path:
    """Racine de l'instance MCP co-installée : `$COCKPIT_HOME/mcp/`."""
    return settings.home / "mcp"


def mcp_venv(settings: Settings) -> Path:
    """Venv Python DÉDIÉ du serveur — ni celui du cockpit, ni celui des outils. Trois venvs parce que
    trois cycles de vie : le wheel monte à la réinjection, les cartes à `tools install`, le serveur à
    l'édition. Les fondre ferait monter les trois dès qu'un seul bouge."""
    return mcp_root(settings) / "venv"


def env_file(settings: Settings) -> Path:
    """L'`EnvironmentFile` du serveur (chmod 600 — il porte le secret HS256 en clair, le serveur n'ayant
    pas de coffre à interroger)."""
    return mcp_root(settings) / f"{SERVER_UNIT}.env"


def unit_dir(scope: str) -> Path:
    return (Path.home() / ".config/systemd/user") if scope == "user" else Path("/etc/systemd/system")


def unit_path(scope: str = "user") -> Path:
    """Où vit l'unité du serveur co-installé, par portée. Même vérité pour qui l'écrit et qui la lit."""
    return unit_dir(scope) / f"{SERVER_UNIT}.service"


def endpoint_url(port: int = DEFAULT_PORT, *, host: str = LOOPBACK_HOST) -> str:
    """L'URL que le cockpit consommera. Le chemin `/mcp` est le contrat du serveur (c'est lui qui liste les
    outils), pas une convention locale."""
    return f"http://{host}:{port}/mcp"


def is_loopback(endpoint: str | None) -> bool:
    """L'endpoint pointe-t-il une adresse de cette machine ? **PUR.** `localhost` et toute la plage
    `127.0.0.0/8` + `::1` comptent ; un nom d'hôte quelconque, non (on ne résout AUCUN DNS — une sonde de
    topologie qui ferait une requête réseau ne serait plus lisible depuis `/api/version`).

    `0.0.0.0` est exclu délibérément : c'est une adresse de **bind**, jamais de destination. La voir dans un
    endpoint consommé signale une confusion de configuration, pas une topologie co-installée."""
    if not endpoint:
        return False
    host = (urlsplit(endpoint).hostname or "").strip()
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:                                 # nom d'hôte non-IP → pas de résolution, pas de oui
        return False


def install_plan(settings: Settings, *, ref: str = SERVER_REF) -> list[dict[str, object]]:
    """Étapes ordonnées `{name, argv}` de l'install du serveur (**PUR** — construit les argv, n'exécute
    rien). Deux passes sur le même paquet, et ce n'est pas une redondance.

    **Le no-op pip-git-SHA**, constaté en vrai sur la VM 9311 le 2026-08-03 et re-applicable ici mot pour
    mot : `pip install --upgrade git+<url>@<ref>` clone, résout la réf, prépare les métadonnées… puis
    **saute l'install** parce que la version installée est identique — et rend **rc 0**.
    `forgemaster-catalogs` est figé à `0.1.0` exactement comme les 3 cartes, donc la version ne discrimine
    JAMAIS : sans 2ᵈᵉ passe, un ré-install qui change de `SERVER_REF` ne bouge pas une ligne en répondant
    « 🟢 ». La 1ʳᵉ passe (`--upgrade`) résout les **dépendances** ; la 2ᵈᵉ force le **code** à la réf
    demandée sans y retoucher (`--force-reinstall --no-deps`)."""
    pip = str(mcp_venv(settings) / "bin" / "pip")
    spec = f"git+{SERVER_REPO}@{ref}"
    return [
        {"name": "pip-server", "argv": [pip, "install", "--upgrade", spec]},
        {"name": "pip-server-pin", "argv": [pip, "install", "--force-reinstall", "--no-deps", spec]},
    ]


def render_env(*, port: int, data_root: Path, secret: str) -> str:
    """L'`EnvironmentFile` du serveur. **PUR.** `VAULT_MCP_HOST` est en **loopback en dur** : un serveur
    co-installé n'a aucune raison d'être joignable depuis le réseau, et le binder en `0.0.0.0` exposerait
    le corpus de l'opérateur à son LAN au premier provisioning. Qui veut servir le réseau déploie le
    serveur pour lui-même (`deploy/` du dépôt amont), il ne co-installe pas.

    `DATA_ROOT` est écrit **explicite** : le résolveur par remontée du serveur cherche un dossier
    `catalogs/`, qu'un corpus typé (`corpus/tech/`) n'a pas — un `DATA_ROOT` absent résoudrait donc une
    mauvaise racine, en silence."""
    return (
        f"# {SERVER_UNIT}.env — posé par `cockpit mcp install`. Porte le secret HS256 : chmod 600.\n"
        "# Ne pas committer. Régénéré à chaque `cockpit mcp install`, jamais édité à la main.\n"
        "VAULT_MCP_TRANSPORT=http\n"
        f"VAULT_MCP_HOST={LOOPBACK_HOST}\n"
        f"VAULT_MCP_PORT={port}\n"
        f"VAULT_MCP_JWT_ISSUER={JWT_ISSUER}\n"
        f"VAULT_MCP_JWT_AUDIENCE={JWT_AUDIENCE}\n"
        f"VAULT_MCP_JWT_SECRET={secret}\n"
        f"DATA_ROOT={data_root}\n"
    )


def render_unit(settings: Settings, *, data_root: Path, scope: str = "user") -> str:
    """L'unité systemd du serveur co-installé. **PUR.** Transposition de `deploy/systemd/
    forgemaster-catalogs.service` du dépôt amont, dont elle garde le nom, le `WorkingDirectory=@DATA_ROOT@`
    et l'`EnvironmentFile`. En portée `system` on épingle l'identité, comme `service.render_unit`."""
    if scope not in ("user", "system"):
        raise ValueError(f"scope inconnu : {scope!r} (attendu 'user' ou 'system')")
    identity = "" if scope == "user" else f"User={getpass.getuser()}\nGroup={getpass.getuser()}\n"
    wanted_by = "default.target" if scope == "user" else "multi-user.target"
    return (
        "[Unit]\n"
        f"Description={SERVER_UNIT} — corpus MCP typé, co-installé avec ce cockpit (loopback + JWT)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"{identity}"
        f"Environment=HOME={Path.home()}\n"
        f"WorkingDirectory={data_root}\n"
        f"EnvironmentFile={env_file(settings)}\n"
        f"ExecStart={mcp_venv(settings) / 'bin' / SERVER_DIST} serve\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "\n"
        "[Install]\n"
        f"WantedBy={wanted_by}\n"
    )


# -- provenance & topologie (lecture LOCALE, zéro réseau, ne lève jamais) ----------------------------

def server_provenance(settings: Settings) -> dict:
    """Provenance du serveur co-installé : `{name, sha, requested_ref, source, reason}`. Délègue à
    `tools.dist_provenance` — c'est le MÊME problème que les cartes (lire PEP 610 dans un `.dist-info`), et
    le ré-implémenter ici ferait diverger deux lectures du même format."""
    return dist_provenance(venv_site_packages(mcp_venv(settings)), SERVER_DIST)


def topology(settings: Settings) -> dict:
    """Quelle topologie MCP cette instance est — `{topology, sha, endpoint, reason}`. Lecture LOCALE, zéro
    réseau, **ne lève jamais** (elle est servie depuis `GET /api/version`).

    Trois états, tous honnêtes :

    - **`none`** — aucun endpoint configuré. C'est un état **normal**, pas une panne : une install sans
      corpus n'a pas d'instance à interroger.
    - **`co-installed`** — un serveur est installé ici ET l'endpoint consommé est en loopback. Seul cas qui
      porte un `sha`, parce que seul cas où le binaire servi est sur ce disque.
    - **`remote`** — un endpoint est consommé, mais ce n'est pas notre serveur local. `sha: null` **avec un
      motif** : le SHA d'un serveur distant ne se lit pas localement, il se DEMANDE (`GET /version` du
      serveur, appel explicite). Un SHA faux coûte plus cher qu'un SHA manquant — il retire le doute qui
      aurait déclenché la vérification.

    Le cas tordu est traité : serveur installé localement mais endpoint pointant ailleurs. La topologie dit
    ce que l'instance **consomme** (`remote`), et le motif signale le serveur local inutilisé — sans ça, un
    opérateur lirait « remote » sur une machine qui fait tourner un serveur, et croirait à un bug."""
    try:
        endpoint = wiring.current_endpoint()
        if not endpoint:
            return {"topology": "none", "sha": None, "endpoint": None,
                    "reason": "aucun endpoint MCP configuré — instance sans corpus à interroger "
                              "(`cockpit mcp install` pour co-installer, `cockpit mcp wire` pour un distant)"}
        prov = server_provenance(settings)
        installed = prov.get("sha") is not None or prov.get("source") == "local-dir"
        if installed and is_loopback(endpoint):
            return {"topology": "co-installed", "sha": prov.get("sha"), "endpoint": endpoint,
                    "reason": prov.get("reason")}
        reason = ("le SHA d'un serveur distant ne se lit pas localement — demande-le lui "
                  "(`GET /version` sous JWT)")
        if installed:
            reason += (f" ; un serveur est pourtant installé ici ({mcp_venv(settings)}) mais cette instance "
                       "ne le consomme pas")
        return {"topology": "remote", "sha": None, "endpoint": endpoint, "reason": reason}
    except Exception:                                  # noqa: BLE001 — sonde servie en HTTP : jamais de 500
        return {"topology": "unknown", "sha": None, "endpoint": None,
                "reason": "topologie MCP illisible sur cet hôte"}


# -- exécution (IMPUR : subprocess via runner injecté, écritures) ------------------------------------

def _default_runner(argv: list[str], *, env: Mapping[str, str] | None, timeout: float) -> RunResult:
    return run(argv, env=env, timeout=timeout, check=False)


def install(settings: Settings, *, data_root: str | Path, port: int = DEFAULT_PORT,
            ref: str = SERVER_REF, scope: str = "user", token_ref: str | None = None,
            runner: Runner | None = None) -> dict:
    """Co-installe le serveur MCP sur cet hôte (IDEMPOTENT, FAIL-LOUD) et câble le cockpit dessus.

    Séquence : venv dédié → pip (2 passes, cf. `install_plan`) → secret HS256 → `EnvironmentFile` 600 →
    unité systemd → `wire()` vers le loopback. Retourne `{ok, steps, unit, env_file, endpoint, hint}` ;
    l'appelant imprime le `hint` — **on n'exécute jamais systemctl depuis la bibliothèque** (même règle
    que `service.install_service`).

    `data_root` est **obligatoire et doit exister** : un serveur démarré sur une racine absente répond
    `200` sur un corpus vide, et cette réussite apparente est pire qu'un refus. `token_ref` (réf du coffre
    vers un PAT de lecture) n'est utile que **tant que le dépôt est privé** — sans lui le clone est
    strictement anonyme, comme celui des 3 cartes.

    Le secret HS256 est **généré ici** quand l'instance n'en a pas : c'est ce qui rend le co-install
    réellement turnkey (aucune valeur à saisir, aucun secret en argv). S'il en a déjà un qui résout, on le
    **réutilise** — le régénérer invaliderait les jetons du serveur qui tourne, à chaque ré-exécution d'une
    commande annoncée idempotente."""
    root = Path(data_root).expanduser()
    if not root.is_dir():
        raise McpInstallError(
            f"racine de donnée introuvable : {root} — `--data-root` est obligatoire et doit exister. "
            "Le co-install pose un LECTEUR de corpus ; le corpus est ta donnée, le cockpit n'en clone "
            "aucun. Sans elle, le serveur démarrerait sur un corpus vide en répondant 200.")
    runner = runner or _default_runner
    steps: list[dict] = []
    report: dict[str, object] = {"ok": True, "steps": steps}

    venv = mcp_venv(settings)
    mcp_root(settings).mkdir(parents=True, exist_ok=True)
    creation: dict = {"name": "venv", "argv": ["python3", "-m", "venv", str(venv)]}
    env = anonymous_env()
    if token_ref:                                       # dépôt encore privé (P6.4 le publie) → auth git
        from cockpit.git.internal import credential_env
        from cockpit.secrets import cred_resolver
        env = credential_env(cred_resolver(settings)(token_ref), base=env)
    for step in [creation, *install_plan(settings, ref=ref)]:
        # `run_step` (partagé avec `tools.install_tools`) attrape aussi les erreurs de TRANSPORT — un
        # `python3` absent, un timeout — qui sans lui remonteraient en exception au milieu d'une install.
        if not run_step(runner, dict(step), env=env, steps=steps, timeout=_STEP_TIMEOUT_S):
            report["ok"] = False                        # jamais un demi-provisioning : on abandonne ici
            report["error"] = f"étape {steps[-1]['name']} rouge (rc {steps[-1].get('exit_code')})"
            return report

    secret = _existing_secret(settings) or secrets.token_urlsafe(_SECRET_BYTES)
    ep = endpoint_url(port)
    try:
        wiring.wire(settings, secret=secret, endpoint=ep, live_env=True)
    except wiring.MCPWireError as exc:                  # coffre incompatible / secret refusé
        raise McpInstallError(f"câblage du cockpit impossible après l'install — {exc}") from exc

    envf = env_file(settings)
    envf.write_text(render_env(port=port, data_root=root, secret=secret), encoding="utf-8")
    envf.chmod(0o600)                                   # porte le secret HS256 en clair (le serveur n'a
    unit = unit_path(scope)                             # pas de coffre) — lecture propriétaire seule
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_unit(settings, data_root=root, scope=scope), encoding="utf-8")

    flag = "--user " if scope == "user" else ""
    sudo = "" if scope == "user" else "sudo "
    report.update({
        "unit": str(unit), "env_file": str(envf), "endpoint": ep,
        "sha": server_provenance(settings).get("sha"),
        "hint": (f"{sudo}systemctl {flag}daemon-reload && "
                 f"{sudo}systemctl {flag}enable --now {SERVER_UNIT}"),
    })
    return report


def cli_install(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit mcp install` : co-installe le serveur, puis IMPRIME le geste systemctl restant (on
    n'active jamais un service depuis la bibliothèque). rc 0 vert · 1 rouge."""
    token_ref = None
    if getattr(args, "token_file", None):               # dépôt encore privé : le PAT ne passe pas en argv
        from cockpit.secrets import build_store
        token = Path(str(args.token_file)).expanduser().read_text(encoding="utf-8").strip()
        token_ref = build_store(settings).put(token, label="mcp-read")
    try:
        report = install(
            settings,
            data_root=str(args.data_root),                    # requis par l'argparse : jamais absent ici
            port=getattr(args, "port", None) or DEFAULT_PORT,
            ref=getattr(args, "ref", None) or SERVER_REF,
            scope="system" if getattr(args, "system", False) else "user",
            token_ref=token_ref,
        )
    except McpInstallError as exc:
        print(f"✗ {exc}")
        return 1
    if not report.get("ok"):
        print(f"✗ co-install interrompue : {report.get('error')}")
        for step in report.get("steps", []):            # type: ignore[union-attr]
            if not step.get("ok"):
                print(f"   {step['name']} → rc {step['exit_code']}\n{step.get('error', '')}")
        return 1
    sha = (report.get("sha") or "?")[:12]
    print(f"✅ {SERVER_UNIT} co-installé (SHA {sha}) → {report['endpoint']}")
    print(f"   unité : {report['unit']}   ·   env (600) : {report['env_file']}")
    print(f"   démarre-le : {report['hint']}")
    return 0


def _existing_secret(settings: Settings) -> str | None:
    """Le secret HS256 déjà câblé sur cette instance, s'il résout — pour qu'une ré-exécution n'invalide pas
    les jetons du serveur qui tourne. **Total** : toute erreur du coffre vaut « pas de secret » (on en
    générera un neuf), jamais une exception dans un chemin d'install."""
    ref = os.environ.get(wiring.ENV_MCP_JWT_SECRET_REF, "")
    if not ref:
        return None
    try:
        from cockpit.secrets import cred_resolver
        value = cred_resolver(settings)(ref)
    except Exception:                                   # noqa: BLE001 — coffre absent/illisible
        return None
    return value if len(value) >= 32 else None
