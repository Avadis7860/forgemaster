"""cli — la porte unifiée `cockpit`. Le câblage argparse est **complet et figé dès la structure** (les
sous-commandes existent, `--help` marche) ; les handlers **délèguent aux couches** en import PARESSEUX,
de sorte que le parser se construit sans tirer fastapi/uvicorn ni aucune couche stub.

Sous-commandes = la surface de la spine (phases du produit) :
  project (create|list|get) · tool (sync) · tools (install|check) · roadmap (add-feature|show) ·
  task (add|next) ·
  dispatch · run · gate (review|verify|toolchain) · merge · onboard · serve · setup · install-service

Chaque handler reçoit `(settings, args)` et retourne un code de sortie. Les couches sont portées : aucun
handler de ce module ne lève plus d'exception de non-implémentation. La seule surface encore différée est
le backend GitHub (`git/github.py`, P6) — `GitBackend` + `InternalGit` restent l'invariant internal-first."""
from __future__ import annotations

import argparse
from collections.abc import Sequence

from cockpit import __version__
from cockpit.config import Settings


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser complet (racines communes + toutes les sous-commandes). PUR, sans import lourd."""
    # registre des types = filesystem (stdlib-only, import léger — le parser reste sans dép lourde).
    # `list_valid_types` filtre par validation (fail-closed) : un overlay cassé n'est pas offert à `--type`.
    from cockpit.provision import list_valid_types
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
    pc.add_argument("--type", dest="project_type",
                    choices=[t["type"] for t in list_valid_types()], default="generic",
                    help="bundle semé par type de projet (base ⊕ overlay) — défaut generic (base seule)")
    pc.add_argument("--from", dest="source_url", metavar="URL",
                    help="adopter un repo existant : cloner son historique réel comme SoT (au lieu de semer)")
    p_project_sub.add_parser("list", parents=[common], help="lister les projets")
    pg = p_project_sub.add_parser("get", parents=[common], help="détail d'un projet")
    pg.add_argument("slug")

    # -- tool ---------------------------------------------------------------------------------------
    p_tool = sub.add_parser("tool", parents=[common],
                            help="outils adoptés (kind=tool) : re-sync pull-only avec l'amont")
    p_tool_sub = p_tool.add_subparsers(dest="action", required=True, metavar="<action>")
    tsy = p_tool_sub.add_parser("sync", parents=[common],
                                help="re-synchroniser un outil avec son amont (ff-only, jamais de push)")
    tsy.add_argument("slug")

    # -- tools (outillage hôte-niveau : maps + Node + qualité py, exposés sur tools/bin pour worker+gate) --
    p_tools = sub.add_parser("tools", parents=[common],
                             help="outillage hôte-niveau que les bundles déclarent (install)")
    p_tools_sub = p_tools.add_subparsers(dest="action", required=True, metavar="<action>")
    # Pas de `--token-file` ici : les 3 cartes sont publiques, le clone est anonyme. Le drapeau a été
    # RETIRÉ plutôt qu'accepté-et-ignoré — un appelant qui le passe encore doit l'apprendre bruyamment.
    p_tools_sub.add_parser("install", parents=[common],
                           help="installer maps + Node + qualité py sous COCKPIT_HOME/tools (anonyme)")
    # `check` RAPPORTE, ne mute rien : le geste de remise à niveau reste `install` (idempotent, --upgrade).
    # Sortie 1 si une carte diffère, 2 si la fraîcheur n'a PAS pu être vérifiée — jamais confondus.
    p_tools_sub.add_parser("check", parents=[common],
                           help="comparer les cartes servies à leur amont (exit 1 diffère, 2 non vérifié)")

    # -- bundle -------------------------------------------------------------------------------------
    p_bundle = sub.add_parser("bundle", parents=[common],
                              help="gestion des bundles : list|validate|show|version|derive")
    p_bundle_sub = p_bundle.add_subparsers(dest="action", required=True, metavar="<action>")
    p_bundle_sub.add_parser("list", parents=[common], help="lister les types du registre + validité")
    blv = p_bundle_sub.add_parser("validate", parents=[common],
                                  help="valider un bundle (ou tous) — exit 1 si un est invalide")
    blv.add_argument("type", nargs="?", help="type à valider ; défaut = tous")
    bsh = p_bundle_sub.add_parser("show", parents=[common], help="détail d'un bundle")
    bsh.add_argument("type")
    bvr = p_bundle_sub.add_parser("version", parents=[common], help="version d'un bundle (ou de tous)")
    bvr.add_argument("type", nargs="?", help="type ; défaut = tous")
    bdv = p_bundle_sub.add_parser("derive", parents=[common],
                                  help="régénérer le seed dérivé d'un type depuis son template corpus")
    bdv.add_argument("--type", help="type à dériver ; défaut = tous les dérivables")
    bdv.add_argument("--check", action="store_true",
                     help="ne rien écrire : exit 1 si l'overlay a dérivé de son template (drift)")

    # -- scaffold -----------------------------------------------------------------------------------
    p_scaffold = sub.add_parser("scaffold", parents=[common],
                                help="maintenance du scaffold d'un projet (re-semer le contrat de run)")
    p_scaffold_sub = p_scaffold.add_subparsers(dest="action", required=True, metavar="<action>")
    psr = p_scaffold_sub.add_parser("reseed", parents=[common],
                                    help="re-matérialiser les fichiers scaffold-owned dans le SoT (dev) — "
                                         "préserve le travail worker ; idempotent")
    psr.add_argument("project")
    psr.add_argument("--feature", metavar="<slug>",
                     help="cibler `feature/<slug>` (feature en vol) au lieu de `dev` — livre le contrat "
                          "de run corrigé sans redrain ; travail worker préservé")

    # -- roadmap ------------------------------------------------------------------------------------
    p_roadmap = sub.add_parser("roadmap", parents=[common], help="roadmap in-repo (features + tasks)")
    p_roadmap_sub = p_roadmap.add_subparsers(dest="action", required=True, metavar="<action>")
    rf = p_roadmap_sub.add_parser("add-feature", parents=[common], help="ajouter une feature")
    rf.add_argument("project")
    rf.add_argument("slug")
    rf.add_argument("--title")
    rf.add_argument("--facet",   # vocab validé par add_feature contre le bundle DU projet (registre)
                    help="facette de dispatch selon le bundle du projet ; défaut = default_facet du bundle")
    rf.add_argument("--depends-on", nargs="*", default=[],
                    help="slugs de features prérequises (DAG inter-feature ; bloque tant que non mergées)")
    rsd = p_roadmap_sub.add_parser("set-deps", parents=[common],
                                   help="éditer le DAG inter-feature d'une feature (REMPLACE ses deps)")
    rsd.add_argument("project")
    rsd.add_argument("feature")
    rsd.add_argument("--depends-on", nargs="*", default=[],
                     help="slugs de features prérequises (remplace l'ensemble ; refuse dangling/cycle/self)")
    rs = p_roadmap_sub.add_parser("show", parents=[common], help="afficher la roadmap d'un projet")
    rs.add_argument("project")
    rc = p_roadmap_sub.add_parser("check", parents=[common],
                                  help="vérifier qu'une roadmap est opérationnelle (gate de complétude)")
    rc.add_argument("project")
    rc.add_argument("--depth", action="store_true",
                    help="ajouter le gate de PROFONDEUR par archétype (chaque axe couvert ou différé)")

    # -- task ---------------------------------------------------------------------------------------
    p_task = sub.add_parser("task", parents=[common], help="tasks d'une feature (DAG depends_on)")
    p_task_sub = p_task.add_subparsers(dest="action", required=True, metavar="<action>")
    ta = p_task_sub.add_parser("add", parents=[common], help="ajouter une task")
    ta.add_argument("feature")
    ta.add_argument("slug")
    ta.add_argument("--title")
    ta.add_argument("--depends-on", nargs="*", default=[], help="ids de tasks prérequises")
    ta.add_argument("--priority", choices=["P0", "P1", "P2", "P3"], default="P1",
                    help="priorité P0-P3 (défaut P1) — classée par priorité effective au résolveur")
    ta.add_argument("--acceptance", required=True,
                    help="critères de DoD (obligatoire) injectés dans le prompt du worker au dispatch")
    tsd = p_task_sub.add_parser("set-deps", parents=[common],
                                help="éditer le DAG intra-feature d'une task existante (REMPLACE ses deps)")
    tsd.add_argument("feature")
    tsd.add_argument("slug")
    tsd.add_argument("--depends-on", nargs="*", default=[],
                     help="ids de tasks prérequises (remplace l'ensemble ; refuse dangling/cycle/self)")
    tn = p_task_sub.add_parser("next", parents=[common], help="prochaine task dispatchable (résolveur DAG)")
    tn.add_argument("feature")

    # -- dispatch -----------------------------------------------------------------------------------
    p_dispatch = sub.add_parser("dispatch", parents=[common], help="dispatcher un worker sur la NEXT task")
    p_dispatch.add_argument("feature")

    # -- cost ---------------------------------------------------------------------------------------
    p_cost = sub.add_parser("cost", parents=[common],
                            help="coût token d'un projet (agrégé + par feature/step)")
    p_cost.add_argument("project")
    p_cost.add_argument("--by-step", action="store_true", help="détailler par step (task) et fix de feature")
    p_cost.add_argument("--json", action="store_true", help="JSON brut de l'agrégation")

    # -- reliability --------------------------------------------------------------------------------
    p_rel = sub.add_parser("reliability", parents=[common],
                           help="fiabilité du gate vert (merges verts vs revert/refix aval marqués)")
    p_rel_sub = p_rel.add_subparsers(dest="action", required=True, metavar="<action>")
    rls = p_rel_sub.add_parser("show", parents=[common],
                               help="taux de fiabilité (projet, ou global si projet omis)")
    rls.add_argument("project", nargs="?", help="projet (omis = agrégat global)")
    rls.add_argument("--json", action="store_true", help="JSON brut de l'agrégation")
    rlm = p_rel_sub.add_parser("mark", parents=[common],
                               help="marquer l'issue aval d'un merge vert (revert/refix constaté)")
    rlm.add_argument("project")
    rlm.add_argument("feature")
    rlm.add_argument("--outcome", required=True, choices=["held", "reverted", "refixed"],
                     help="issue aval marquée (held = annuler une marque)")
    rlm.add_argument("--note", help="raison libre de la marque")
    rlm.add_argument("--sha", help="cibler un merge précis (défaut : le plus récent de la feature)")
    rlm.add_argument("--json", action="store_true", help="JSON de la ligne marquée")

    # -- run ----------------------------------------------------------------------------------------
    p_run = sub.add_parser("run", parents=[common],
                           help="drainer la roadmap d'un projet en parallèle (features indépendantes)")
    p_run.add_argument("project")
    p_run.add_argument("--max-parallel", type=int, default=2,
                       help="nombre max de workers concurrents (features en parallèle ; défaut 2)")

    # -- abort --------------------------------------------------------------------------------------
    p_abort = sub.add_parser("abort", parents=[common],
                             help="arrêter le run en cours d'un projet (workers tués, mutex libéré)")
    p_abort.add_argument("project")
    p_abort.add_argument("--feature", default=None,
                         help="ne cibler que les workers de cette feature (défaut : tout le run du projet)")

    # -- refix --------------------------------------------------------------------------------------
    p_refix = sub.add_parser("refix", parents=[common],
                             help="dispatcher UNE passe de correction bornée sur un gate rouge (offre)")
    p_refix.add_argument("project")
    p_refix.add_argument("feature")

    # -- redrain ------------------------------------------------------------------------------------
    p_redrain = sub.add_parser("redrain", parents=[common],
                               help="re-drainer une feature à base périmée (worktree purgé, tasks → todo, "
                                    "branche réinitialisée sur dev au prochain dispatch)")
    p_redrain.add_argument("project")
    p_redrain.add_argument("feature")

    # -- interview ----------------------------------------------------------------------------------
    p_interview = sub.add_parser("interview", parents=[common],
                                 help="mener l'interview terminale interactive du socle (1ʳᵉ session)")
    p_interview.add_argument("project")

    # -- inspire ------------------------------------------------------------------------------------
    p_inspire = sub.add_parser("inspire", parents=[common],
                               help="appliquer un template UI de référence à un projet (cible visuelle)")
    p_inspire.add_argument("project")
    p_inspire.add_argument("template", help="slug d'un template de la vitrine (cf. /templates)")

    # -- upload -------------------------------------------------------------------------------------
    p_upload = sub.add_parser("upload", parents=[common],
                              help="déposer un fichier (asset/doc) dans un projet (docs/design/<dest>/)")
    p_upload.add_argument("project")
    p_upload.add_argument("path", help="chemin local du fichier à déposer")
    p_upload.add_argument("--dest", default="brand",
                          help="sous-dossier sous docs/design/ (défaut: brand)")
    p_upload.add_argument("--feature", default=None,
                          help="cible un worktree actif nommé (défaut: auto — actif sinon voie forge)")

    # -- deploy -------------------------------------------------------------------------------------
    p_deploy = sub.add_parser("deploy", parents=[common],
                              help="cycle de vie du service d'un projet (backend compose, P2 runtime)")
    p_deploy_sub = p_deploy.add_subparsers(dest="action", required=True, metavar="<action>")
    for _act, _help in (("up", "build + démarrer le service (up -d --build)"),
                        ("down", "arrêter + retirer conteneurs/réseau (down)"),
                        ("restart", "redémarrer les conteneurs (restart)"),
                        ("status", "réconcilier l'état live (ps) — read-only")):
        _pd = p_deploy_sub.add_parser(_act, parents=[common], help=_help)
        _pd.add_argument("slug")
        _pd.add_argument("branch", choices=["main", "dev"], help="déploiement : main (prod) ou dev (preview)")

    # -- gate ---------------------------------------------------------------------------------------
    p_gate = sub.add_parser("gate", parents=[common], help="gate de review / vérification")
    p_gate_sub = p_gate.add_subparsers(dest="action", required=True, metavar="<action>")
    gr = p_gate_sub.add_parser("review", parents=[common], help="ingère un verdict Tier-1 (JSON stdin)")
    gr.add_argument("feature")
    grd = p_gate_sub.add_parser("review-dispatch", parents=[common],
                                help="DISPATCHE le review-worker Tier-1 (produit le verdict SHA-bound)")
    grd.add_argument("feature")
    gv = p_gate_sub.add_parser("verify", parents=[common], help="gate feature-verified e2e")
    gv.add_argument("feature")
    gt = p_gate_sub.add_parser("toolchain", parents=[common],
                               help="gate Tier-0 natif (toolchain front/backend déterministe)")
    gt.add_argument("feature")
    gwd = p_gate_sub.add_parser("woaw-dispatch", parents=[common],
                                help="DISPATCHE le juge esthétique woaw (verdict advisory SHA-bound)")
    gwd.add_argument("feature")

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

    # -- bootstrap ----------------------------------------------------------------------------------
    pb = sub.add_parser("bootstrap", parents=[common],
                        help="adopter les outils du framework déclarés dans le manifeste (idempotent)")
    pb.add_argument("--init", action="store_true", help="écrire un manifeste gabarit puis quitter")
    pb.add_argument("--token-file",
                    help="fichier d'un token de lecture partagé (voie fichier — jamais en argv)")

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

    # -- snapshot -----------------------------------------------------------------------------------
    p_snap = sub.add_parser("snapshot", parents=[common],
                            help="instantané restaurable de l'état (à prendre AVANT une MAJ)")
    p_snap_sub = p_snap.add_subparsers(dest="action", required=True, metavar="<action>")
    p_snap_sub.add_parser("create", parents=[common],
                          help="prendre un instantané (base + réglages + coffre chiffré)")
    p_snap_sub.add_parser("list", parents=[common],
                          help="lister les instantanés — les incomplets sont signalés, pas masqués")
    p_snap_res = p_snap_sub.add_parser(
        "restore", parents=[common],
        help="remettre un instantané (lance son `restore.py` — l'état remplacé est mis de côté)")
    p_snap_res.add_argument("snapshot", help="nom de l'instantané (ou chemin d'un dossier)")
    p_snap_res.add_argument("--dry-run", action="store_true",
                            help="dire ce qui serait remis, ne rien écrire")

    # -- update -------------------------------------------------------------------------------------
    p_up = sub.add_parser("update", parents=[common],
                          help="poser un wheel LOCAL en bleu/vert — retour arrière auto s'il ne sert pas")
    p_up_sub = p_up.add_subparsers(dest="action", required=True, metavar="<action>")
    p_up_ap = p_up_sub.add_parser(
        "apply", parents=[common],
        help="installer un wheel à côté, le prouver, basculer — et revenir seul si le vivant ne répond plus")
    p_up_ap.add_argument("--wheel", required=True, help="le wheel à poser (fichier local ; aucun réseau)")
    p_up_ap.add_argument("--dry-run", action="store_true", help="dire ce qui serait fait, ne rien lancer")
    p_up_ap.add_argument("--detach", action="store_true",
                         help="ne pas suivre le journal (la MAJ tourne quand même en arrière-plan)")
    p_up_ap.add_argument("--system", action="store_true", help="unité systemd système (exige root)")
    p_up_ap.add_argument("--unit", help="chemin de l'unité systemd (défaut : celle de la portée)")
    p_up_ap.add_argument("--service", default="cockpit", help="nom de l'unité à arrêter/relancer")
    p_up_ap.add_argument("--systemctl", default="systemctl", help="binaire systemctl (injectable)")

    # -- doctor -------------------------------------------------------------------------------------
    sub.add_parser("doctor", parents=[common],
                   help="sonder la présence de l'outillage déclaré par les facettes (rc 0 sain / 1 manquant)")

    # -- mcp ----------------------------------------------------------------------------------------
    p_mcp = sub.add_parser("mcp", parents=[common], help="câbler un MCP de corpus (mcp-catalogs)")
    p_mcp_sub = p_mcp.add_subparsers(dest="action", required=True, metavar="<action>")
    pmw = p_mcp_sub.add_parser("wire", parents=[common],
                               help="poser la ref du secret + l'endpoint MCP dans cockpit.env")
    pmw.add_argument("--secret-file", help="fichier du secret HMAC partagé (jamais en argv)")
    pmw.add_argument("--secret-ref", help="UUID d'un secret déjà dans le coffre (voie BWS)")
    pmw.add_argument("--endpoint", help="endpoint MCP (défaut : l'instance mcp-catalogs)")

    return parser


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.resolve(home=getattr(args, "home", None),
                            projects_root=getattr(args, "projects_root", None))


def _autoload_env(args: argparse.Namespace) -> None:
    """Charge `$COCKPIT_HOME/cockpit.env` dans `os.environ` AVANT de résoudre les Settings — parité CLI ↔
    service (le service lit l'`EnvironmentFile`, la CLI doit voir la même config, dont le câblage MCP). Sinon
    un `cockpit dispatch` en shell perdait le MCP en silence. Home résolu comme `_settings` (flag > env >
    défaut) ; le fichier ne surcharge jamais une clé déjà dans l'environnement réel."""
    import os
    from pathlib import Path

    from cockpit.config import DEFAULT_HOME, ENV_HOME
    from cockpit.service import load_env_file
    home = getattr(args, "home", None) or os.environ.get(ENV_HOME) or DEFAULT_HOME
    load_env_file(Path(os.path.expanduser(str(home))) / "cockpit.env")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, charge cockpit.env (parité service), résout les Settings, dispatche vers le handler."""
    args = build_parser().parse_args(argv)
    _autoload_env(args)
    settings = _settings(args)
    handler = _HANDLERS[args.command]
    return handler(settings, args)


# --- handlers (délégation paresseuse — n'importent la couche qu'à l'appel) -------------------------

def _h_project(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.projects import registry
    return registry.cli_dispatch(settings, args)


def _h_tool(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import toolsync
    return toolsync.cli_dispatch(settings, args)


def _h_tools(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import tools
    return tools.cli_dispatch(settings, args)


def _h_bundle(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.provision import manage
    return manage.cli_dispatch(settings, args)


def _h_roadmap(settings: Settings, args: argparse.Namespace) -> int:
    if args.action == "check":
        from cockpit.roadmap import check
        return check.cli_dispatch(settings, args)
    from cockpit.roadmap import model
    return model.cli_dispatch(settings, args)


def _h_task(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.roadmap import resolver
    return resolver.cli_dispatch(settings, args)


def _h_cost(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.dispatch import cost
    return cost.cli_dispatch(settings, args)


def _h_reliability(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.db import merge_outcomes
    return merge_outcomes.cli_dispatch(settings, args)


def _h_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.dispatch import worker
    return worker.cli_dispatch(settings, args)


def _h_run(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.dispatch import orchestrator
    return orchestrator.cli_dispatch(settings, args)


def _h_abort(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.dispatch import abort
    return abort.cli_dispatch(settings, args)


def _h_refix(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.dispatch import refix
    return refix.cli_dispatch(settings, args)


def _h_redrain(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.dispatch import redrain
    return redrain.cli_dispatch(settings, args)


def _h_scaffold(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.provision import reseed
    return reseed.cli_dispatch(settings, args)


def _h_interview(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import interview
    return interview.cli_dispatch(settings, args)


def _h_inspire(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit inspire <projet> <template>` : applique un template UI de référence servi comme cible
    visuelle (crée la feature+task de customisation + sème la graine). Import fastapi-free (spine) : seul
    `web_dist_dir` (résolveur de chemin pur) + le cœur `apply_template`."""
    from cockpit.daemon.app import web_dist_dir
    from cockpit.db import store
    from cockpit.design.apply import apply_template
    source = web_dist_dir() / "templates" / args.template
    conn = store.open_db(settings)
    try:
        report = apply_template(conn, settings, project=args.project, template_slug=args.template,
                                source_dir=source)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    files = ", ".join(report["files"]) or "(graine vide)"
    print(f"template « {report['template']} » appliqué à {report['project']} → feature {report['feature']} "
          f"(graine docs/design/{report['template']}/ : {files}). "
          f"Dispatch la customisation : `cockpit dispatch {report['project']}/{report['feature']}`.")
    return 0


def _h_upload(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit upload <projet> <chemin> [--dest <slug>] [--feature <f>]` : dépose le fichier local dans
    le projet sous `docs/design/<dest>/` via le **même** cœur que la route HTTP (`ingest_upload`). Import
    fastapi-free (spine) : seul `store` + le cœur `content.ingest`. Bornes : type/taille/secret/traversal
    remontent comme erreurs (imprimées → code 1) ; parité stricte avec `POST /api/projects/{slug}/upload`."""
    from pathlib import Path

    from cockpit.content.ingest import ingest_upload
    from cockpit.db import store
    src = Path(args.path)
    try:
        data = src.read_bytes()
    except OSError as exc:
        print(f"erreur : fichier illisible : {exc}")
        return 1
    conn = store.open_db(settings)
    try:
        report = ingest_upload(conn, settings, project=args.project, filename=src.name,
                               data=data, dest_slug=args.dest, feature=args.feature)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    if report["mode"] == "noop":
        print(f"fichier vide : rien déposé dans {args.project} (aucune feature créée).")
        return 0
    print(f"« {report['file']} » déposé dans {report['project']} → {report['path']} "
          f"(feature {report['feature']}, branche {report['branch']}, mode {report['mode']}, "
          f"commit {report['commit'] or '(rien à committer)'}).")
    return 0


def _h_deploy(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.runtime import engine
    return engine.cli_dispatch(settings, args)


def _h_gate(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.dispatch import reviewer
    from cockpit.dispatch import woaw as woaw_dispatch
    from cockpit.gate import review, toolchain, verify
    # `review` INGÈRE un verdict (JSON stdin) ; `review-dispatch` le PRODUIT (dispatch du review-worker).
    # `woaw-dispatch` PRODUIT le verdict esthétique advisory (dispatch du juge woaw sur le rendu).
    mod = {"review": review, "review-dispatch": reviewer,
           "verify": verify, "toolchain": toolchain, "woaw-dispatch": woaw_dispatch}[args.action]
    return mod.cli_dispatch(settings, args)


def _h_merge(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.gate import merge
    return merge.cli_dispatch(settings, args)


def _h_onboard(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import onboarding
    return onboarding.cli_dispatch(settings, args)


def _h_bootstrap(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import bootstrap
    return bootstrap.cli_dispatch(settings, args)


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
    print(f"UI buildée → {dist}.")
    for line in webbuild.ensure_maps():  # from-clone : câble les 4 cartes (Flow codemap + anti-archéologie)
        print(f"  {line}")
    print("Lance `cockpit serve`.")
    return 0


def _h_install_service(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import service
    scope = "system" if args.system else "user"
    unit, env, hint = service.install_service(settings, host=args.host, port=args.port, scope=scope)
    print(f"unité systemd écrite → {unit}")
    print(f"EnvironmentFile     → {env} (réglages : store, bind ; aucun secret)")
    link = service.stable_link(settings)
    if link.is_symlink():
        print(f"lien stable         → {link} → {link.resolve()} "
              f"(l'unité le lance ; c'est lui que `cockpit update apply` bascule)")
    else:
        print(f"lien stable         → non posé ({link}) : ce cockpit ne tourne pas dans un venv qui porte "
              f"la commande `cockpit`. `cockpit update apply` refusera — c'est voulu.")
    print(f"active-la           : {hint}")
    return 0


def _h_snapshot(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import snapshot
    return snapshot.cli_dispatch(settings, args)


def _h_update(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import update
    return update.cli_dispatch(settings, args)


def _h_doctor(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit import doctor
    return doctor.cli_dispatch(settings, args)


def _h_mcp(settings: Settings, args: argparse.Namespace) -> int:
    from cockpit.provision import mcp
    return mcp.cli_dispatch(settings, args)


_HANDLERS = {
    "project": _h_project,
    "tool": _h_tool,
    "tools": _h_tools,
    "bundle": _h_bundle,
    "roadmap": _h_roadmap,
    "task": _h_task,
    "dispatch": _h_dispatch,
    "cost": _h_cost,
    "reliability": _h_reliability,
    "run": _h_run,
    "abort": _h_abort,
    "refix": _h_refix,
    "redrain": _h_redrain,
    "scaffold": _h_scaffold,
    "interview": _h_interview,
    "inspire": _h_inspire,
    "upload": _h_upload,
    "deploy": _h_deploy,
    "gate": _h_gate,
    "merge": _h_merge,
    "onboard": _h_onboard,
    "bootstrap": _h_bootstrap,
    "serve": _h_serve,
    "setup": _h_setup,
    "install-service": _h_install_service,
    "snapshot": _h_snapshot,
    "update": _h_update,
    "doctor": _h_doctor,
    "mcp": _h_mcp,
}
