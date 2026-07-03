"""cli — la porte unifiée `cockpit`. Le câblage argparse est **complet et figé dès la structure** (les
sous-commandes existent, `--help` marche) ; les handlers **délèguent aux couches** en import PARESSEUX,
de sorte que le parser se construit sans tirer fastapi/uvicorn ni aucune couche stub.

Sous-commandes = la surface de la spine (phases du produit) :
  project (create|list|get) · roadmap (add-feature|show) · task (add|next) ·
  dispatch · gate (review|verify|toolchain) · merge · onboard · serve · setup · install-service

Chaque handler reçoit `(settings, args)` et retourne un code de sortie. Tant que les couches sont des
stubs, l'appel lève `NotImplementedError("port: … — #N")` — c'est voulu (le câblage est prouvé, la
logique est le chunk suivant)."""
from __future__ import annotations

import argparse
from collections.abc import Sequence

from cockpit import __version__
from cockpit.config import Settings


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser complet (racines communes + toutes les sous-commandes). PUR, sans import lourd."""
    parser = argparse.ArgumentParser(prog="cockpit", description="Forge/orchestrateur local de projets.")
    parser.add_argument("--version", action="version", version=f"cockpit {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="racine d'état du cockpit (défaut: $COCKPIT_HOME ou ~/.cockpit)")
    common.add_argument("--projects-root", help="racine des repos projets (défaut: $COCKPIT_PROJECTS_ROOT)")

    sub = parser.add_subparsers(dest="command", required=True, metavar="<commande>")

    # -- project ------------------------------------------------------------------------------------
    p_project = sub.add_parser("project", parents=[common], help="registre des projets")
    p_project_sub = p_project.add_subparsers(dest="action", required=True, metavar="<action>")
    pc = p_project_sub.add_parser("create", parents=[common], help="créer un projet")
    pc.add_argument("slug")
    pc.add_argument("--name")
    pc.add_argument("--kind", choices=["project", "tool"], default="project",
                    help="classification : projet travaillé (défaut) ou outil générique du framework")
    p_project_sub.add_parser("list", parents=[common], help="lister les projets")
    pg = p_project_sub.add_parser("get", parents=[common], help="détail d'un projet")
    pg.add_argument("slug")

    # -- roadmap ------------------------------------------------------------------------------------
    p_roadmap = sub.add_parser("roadmap", parents=[common], help="roadmap in-repo (features + tasks)")
    p_roadmap_sub = p_roadmap.add_subparsers(dest="action", required=True, metavar="<action>")
    rf = p_roadmap_sub.add_parser("add-feature", parents=[common], help="ajouter une feature")
    rf.add_argument("project")
    rf.add_argument("slug")
    rf.add_argument("--title")
    rs = p_roadmap_sub.add_parser("show", parents=[common], help="afficher la roadmap d'un projet")
    rs.add_argument("project")

    # -- task ---------------------------------------------------------------------------------------
    p_task = sub.add_parser("task", parents=[common], help="tasks d'une feature (DAG depends_on)")
    p_task_sub = p_task.add_subparsers(dest="action", required=True, metavar="<action>")
    ta = p_task_sub.add_parser("add", parents=[common], help="ajouter une task")
    ta.add_argument("feature")
    ta.add_argument("slug")
    ta.add_argument("--title")
    ta.add_argument("--depends-on", nargs="*", default=[], help="ids de tasks prérequises")
    tn = p_task_sub.add_parser("next", parents=[common], help="prochaine task dispatchable (résolveur DAG)")
    tn.add_argument("feature")

    # -- dispatch -----------------------------------------------------------------------------------
    p_dispatch = sub.add_parser("dispatch", parents=[common], help="dispatcher un worker sur la NEXT task")
    p_dispatch.add_argument("feature")

    # -- gate ---------------------------------------------------------------------------------------
    p_gate = sub.add_parser("gate", parents=[common], help="gate de review / vérification")
    p_gate_sub = p_gate.add_subparsers(dest="action", required=True, metavar="<action>")
    gr = p_gate_sub.add_parser("review", parents=[common], help="verdict Tier-1 lié au SHA")
    gr.add_argument("feature")
    gv = p_gate_sub.add_parser("verify", parents=[common], help="gate feature-verified e2e")
    gv.add_argument("feature")
    gt = p_gate_sub.add_parser("toolchain", parents=[common],
                               help="gate Tier-0 natif (toolchain front/backend déterministe)")
    gt.add_argument("feature")

    # -- merge --------------------------------------------------------------------------------------
    p_merge = sub.add_parser("merge", parents=[common], help="merger une feature complète (+ cleanup)")
    p_merge.add_argument("feature")
    p_merge.add_argument("--go", action="store_true", help="GO humain — sans lui, un gate vert affiche hold")

    # -- onboard ------------------------------------------------------------------------------------
    p_onboard = sub.add_parser("onboard", parents=[common],
                               help="check config-requise + liaison des tokens (self-hosted)")
    p_onboard_sub = p_onboard.add_subparsers(dest="action", required=False, metavar="<action>")
    p_onboard_sub.add_parser("status", parents=[common], help="ce qui manque au 1er démarrage (défaut)")
    ol = p_onboard_sub.add_parser("link", parents=[common], help="lier un token à un projet")
    ol.add_argument("project")
    ol.add_argument("--token-file", help="fichier contenant le token (voie fichier — jamais en argv)")
    ol.add_argument("--ref", help="référence BWS (UUID bring-your-own — voie BWS)")
    ol.add_argument("--label", help="libellé humain optionnel (jamais le secret)")
    ou = p_onboard_sub.add_parser("unlink", parents=[common], help="délier le token d'un projet")
    ou.add_argument("project")

    # -- serve --------------------------------------------------------------------------------------
    p_serve = sub.add_parser("serve", parents=[common], help="démarrer le daemon FastAPI (web + API)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8700)

    # -- setup --------------------------------------------------------------------------------------
    ps = sub.add_parser("setup", parents=[common],
                        help="build l'UI depuis les sources (from-clone ; inutile pour un wheel packagé)")
    ps.add_argument("--no-clean", action="store_true", help="npm install au lieu de npm ci")

    # -- install-service ----------------------------------------------------------------------------
    pi = sub.add_parser("install-service", parents=[common],
                        help="installer une unité systemd pour `cockpit serve` (self-hosted)")
    pi.add_argument("--system", action="store_true", help="unité système (root) au lieu d'un service user")
    pi.add_argument("--host", default="127.0.0.1", help="bind du daemon (0.0.0.0 pour le réseau local)")
    pi.add_argument("--port", type=int, default=8700)

    return parser


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.resolve(home=getattr(args, "home", None),
                            projects_root=getattr(args, "projects_root", None))


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, résout les Settings, dispatche vers le handler de la couche (import paresseux)."""
    args = build_parser().parse_args(argv)
    settings = _settings(args)
    handler = _HANDLERS[args.command]
    return handler(settings, args)


# --- handlers (délégation paresseuse — n'importent la couche qu'à l'appel) -------------------------

def _h_project(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.projects import registry
    return registry.cli_dispatch(settings, args)


def _h_roadmap(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.roadmap import model
    return model.cli_dispatch(settings, args)


def _h_task(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.roadmap import resolver
    return resolver.cli_dispatch(settings, args)


def _h_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.dispatch import worker
    return worker.cli_dispatch(settings, args)


def _h_gate(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.gate import review, toolchain, verify
    mod = {"review": review, "verify": verify, "toolchain": toolchain}[args.action]
    return mod.cli_dispatch(settings, args)


def _h_merge(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.gate import merge
    return merge.cli_dispatch(settings, args)


def _h_onboard(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import onboarding
    return onboarding.cli_dispatch(settings, args)


def _h_serve(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.daemon import app
    return app.serve(settings, host=args.host, port=args.port)


def _h_setup(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import webbuild
    web = webbuild.find_web_dir()
    if web is None:                                      # install wheel pure : l'UI est déjà empaquetée
        print("UI déjà empaquetée (install wheel) — rien à builder. Lance `cockpit serve`.")
        return 0
    try:
        dist = webbuild.build_front(web, clean_install=not args.no_clean)
    except webbuild.FrontBuildError as exc:
        print(f"erreur : {exc}")
        return 1
    print(f"UI buildée → {dist}. Lance `cockpit serve`.")
    return 0


def _h_install_service(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import service
    scope = "system" if args.system else "user"
    unit, env, hint = service.install_service(settings, host=args.host, port=args.port, scope=scope)
    print(f"unité systemd écrite → {unit}")
    print(f"EnvironmentFile     → {env} (réglages : store, bind ; aucun secret)")
    print(f"active-la           : {hint}")
    return 0


_HANDLERS = {
    "project": _h_project,
    "roadmap": _h_roadmap,
    "task": _h_task,
    "dispatch": _h_dispatch,
    "gate": _h_gate,
    "merge": _h_merge,
    "onboard": _h_onboard,
    "serve": _h_serve,
    "setup": _h_setup,
    "install-service": _h_install_service,
}
