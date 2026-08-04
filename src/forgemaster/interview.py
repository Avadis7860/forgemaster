"""interview — point d'entrée **terminal interactif** de la 1ʳᵉ session d'un projet neuf.

Le dispatch (`worker.dispatch_next`) refuse le spawn headless d'une task `mode=interactive` et la surface en
`needs_interview` : une interview / un cadrage ne se mène pas en `claude -p` (aucun interlocuteur). CE module
est le pendant humain — `forgemaster interview <projet>` lance un `claude` **interactif** (TTY hérité, jamais
`-p`) dans le worktree du socle, seedé d'un prompt préparé qui **pointe le skill semé**
`first-session-interview` (la méthode design-first vit dans le bundle, PAS dans ce moteur générique —
invariant « le socle porte le hint »). L'humain mène l'interview, remplit la doc de design, puis AUTHORE la
roadmap de travail (`roadmap-decompose` → `forgemaster roadmap add-feature`). À la sortie, la forge
**vérifie**
(roadmap check vert + ≥1 feature de travail) et clôt le socle — verified, pas trusted.

Transport injectable (`Launcher`) : les tests ne lancent JAMAIS un vrai `claude`. Zéro I/O réseau au build
du prompt (déterministe, comme `roadmap/prompt.py`).
"""
from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from pathlib import Path

from forgemaster import auth
from forgemaster.config import Settings
from forgemaster.core import ids
from forgemaster.db import store
from forgemaster.dispatch import worktree
from forgemaster.git.backend import GitBackend
from forgemaster.git.identity import resolve_identity
from forgemaster.git.internal import InternalGit
from forgemaster.projects import registry
from forgemaster.projects.registry import get_project, sot_path_for
from forgemaster.provision.mcp import inject_mcp_config
from forgemaster.roadmap import check, model, resolver
from forgemaster.tools import cli_env

# launcher(argv, *, cwd, env) -> returncode. Défaut = subprocess TTY hérité ; injecté en test (jamais un
# vrai `claude`). Distinct du `Runner` du worker (qui CAPTURE le stdout `-p`) : ici stdout/stdin/stderr sont
# HÉRITÉS (l'humain parle à `claude` dans son terminal), rien n'est capturé.
Launcher = Callable[..., int]


def build_interview_argv(prompt: str, *, model_name: str | None = None,
                         session_id: str | None = None) -> list[str]:
    """Argv d'un `claude` **interactif** (jamais `-p`) seedé du `prompt` comme premier message. Interactif par
    défaut (le TTY est hérité au lancement). `session_id` (v14) épingle l'id de session (`--session-id`) →
    transcript déterministe `~/.claude/projects/…/<session_id>.jsonl` pour sommer son coût token. PUR."""
    argv = ["claude"]
    if model_name:
        argv += ["--model", model_name]
    if session_id:
        argv += ["--session-id", session_id]
    argv.append(prompt)                 # positionnel = premier message ; démarre la session interactive
    return argv


def build_interview_prompt(project: dict, feature: dict, task: dict) -> str:
    """Prompt préparé de l'interview, **déterministe** : il POINTE le skill semé `first-session-interview` (la
    méthode) et rend l'`acceptance` de la task verbatim — AUCUNE logique design-first ici (elle vit dans le
    bundle). Le moteur pose l'amorce ; le skill porte le quoi. PUR."""
    slug = project["slug"]
    acceptance = (task.get("acceptance") or "").strip()
    acc_block = f"\n\n## Critères d'acceptation (DoD)\n{acceptance}" if acceptance else ""
    return (
        f"# Interview de 1ʳᵉ session — {slug} ({project.get('name') or slug})\n"
        f"Tu es dans une session TERMINALE INTERACTIVE : un humain est en face (ce n'est PAS du headless — "
        f"pose-lui les questions dont tu as besoin). Feature : {feature['slug']} "
        f"({feature.get('title') or feature['slug']}) · Task : {task['slug']} (interactive).\n\n"
        f"Suis le skill **`first-session-interview`** semé dans ce repo "
        f"(`.claude/skills/first-session-interview/`) — il porte la méthode. En résumé :\n"
        f"1. Mène l'interview avec l'humain pour cadrer l'intention du projet.\n"
        f"2. Remplis la doc de design ciblée par les critères ci-dessous (plus aucun « à renseigner »).\n"
        f"3. Dérive la roadmap de travail : applique `roadmap-decompose` et AUTHORE les features via "
        f"`forgemaster roadmap add-feature {slug} <feature> --facet <f> …` puis "
        f"`forgemaster task add {slug}/<feature> <task> --acceptance \"…\"`, jusqu'à "
        f"`forgemaster roadmap check {slug}` vert avec ≥1 feature de travail.\n"
        f"4. Préviens l'humain à la fin — la forge vérifie (roadmap check + ≥1 feature) et clôt le socle."
        f"{acc_block}\n"
    )


def resolve_interview(conn, project: str) -> tuple[dict, dict] | None:
    """La (feature, next task) `interactive` en attente pour `project`, ou `None` s'il n'y en a aucune
    (honnête fail-soft). Parcourt les features, prend la NEXT task READY de chacune (résolveur DAG) et retient
    la première dont `mode == interactive`. Ordre stable (features triées par slug dans `list_features`)."""
    for feat in model.list_features(conn, project):
        index = resolver.index_for_feature(conn, f"{project}/{feat['slug']}")
        if not index:
            continue
        nxt = resolver.resolve_next(index)
        if nxt is not None and nxt.get("mode") == "interactive":
            return feat, nxt
    return None


def socle_feature(conn, project: str) -> dict | None:
    """La feature SOCLE d'un projet = celle qui porte une task `mode=='interactive'` — marqueur DURABLE,
    indépendant du statut des tasks. À la différence de `resolve_interview` (qui exige une NEXT task READY et
    rend donc `None` dès que le socle est clos), ce prédicat identifie le socle **même clôturé** : il sert au
    gate de drain aval (le socle non-mergé bloque les features de travail). `None` si le projet n'a pas de
    socle interactif (projet mûr / control-plane) → rien à gater. Ordre stable (features triées par slug)."""
    for feat in model.list_features(conn, project):
        if any(t.get("mode") == "interactive" for t in model.list_tasks(conn, feat["id"])):
            return feat
    return None


def _has_work_feature(conn, project: str, socle_slug: str) -> bool:
    """≥1 feature de travail existe = au moins une feature AUTRE que le socle (un projet neuf n'a que le socle
    semé ; `roadmap-decompose` en ajoute). C'est le critère binaire objectif de « l'interview a produit »."""
    return any(f["slug"] != socle_slug for f in model.list_features(conn, project))


def _depth_issues(conn, settings: Settings, project: str, *, cwd: Path | None = None) -> list[check.Issue]:
    """Défauts de PROFONDEUR d'archétype à la clôture du socle (chaque axe couvert-ou-différé — le hard-gate
    qui empêche une roadmap PLATE de clore, pas seulement le `--depth` opt-in). Déférals lus du worktree
    réservé (`cwd`, édition d'interview `.forgemaster/deferred-axes.yaml` non commitée) sinon du SoT commité.
    No-op honnête (liste vide) si l'archétype du projet n'a pas d'axes catalogués."""
    deferred = check.load_deferred_axes(settings, project, cwd=cwd)
    return check.check_depth_axes(conn, project, deferred=deferred)


def verify_and_complete(conn, settings: Settings, project: str, feature: dict, *,
                        wt_path: Path | None = None) -> dict:
    """VÉRIFIE le socle après la session (verified, pas trusted) : `roadmap check` vert, **profondeur
    d'archétype couverte-ou-différée** ET ≥1 feature de travail → clôt les tasks du socle en `done`
    (l'interview a rempli la doc, decompose a authoré la roadmap, la passe B a statué chaque axe). Sinon
    laisse le socle `todo` et rend ce qui manque (complétude + profondeur). Les axes différés sont lus du
    worktree réservé (`wt_path`, `.forgemaster/deferred-axes.yaml` non encore commité). Rend
    `{completed, issues, work_feature, socle_tasks_closed}` (`socle_tasks_closed` = nombre de tasks
    RÉELLEMENT transitionnées → done, pour le compte-rendu humain de la réconciliation)."""
    issues = check.check_roadmap(conn, project) + _depth_issues(conn, settings, project, cwd=wt_path)
    work = _has_work_feature(conn, project, feature["slug"])
    completed = not issues and work
    closed = 0
    if completed:
        cur = conn.execute("UPDATE tasks SET status = 'done' "
                           "WHERE feature_id = ? AND status NOT IN ('done', 'cancelled')",
                           (feature["id"],))
        closed = cur.rowcount
        conn.commit()
    return {"completed": completed, "issues": [f"{i.kind} {i.feature}/{i.task or '-'}" for i in issues],
            "work_feature": work, "socle_tasks_closed": closed}


def _socle_worked(conn, settings: Settings, project: str, feature: dict, *,
                  wt_path: Path | None = None) -> bool:
    """Le socle est-il DÉJÀ objectivement travaillé (l'interview a produit) — même critère que la clôture de
    `verify_and_complete` (`roadmap check` vert, profondeur d'archétype couverte ET ≥1 feature de travail),
    mais SANS muter. Prédicat de la réconciliation idempotente : vrai → on peut clore sans (re)lancer
    l'interview. Sans `wt_path` (appelé avant réservation), les axes différés sont lus du SoT commité."""
    return (_has_work_feature(conn, project, feature["slug"])
            and not check.check_roadmap(conn, project)
            and not _depth_issues(conn, settings, project, cwd=wt_path))


def _commit_design(git: GitBackend, project: str, feature: dict, wt_path) -> str | None:
    """Rend la doc de design éditée durable sur la branche du socle (arbre net → no-op propre). Retourne le
    SHA du commit créé (ou `None` si rien à committer) — remonté au compte-rendu de la réconciliation."""
    return git.commit_worktree(wt_path, message=f"docs({feature['slug']}): interview de 1ʳᵉ session",
                               identity=resolve_identity(project, worktree.WORKTREE_BASE, role="worker"))


def reconcile_socle(conn, settings: Settings, *, project: str, git: GitBackend | None = None) -> dict | None:
    """Clôt le socle d'un projet SANS session interactive, quand une interview a DÉJÀ produit (design rempli +
    roadmap authorée : `roadmap check` vert + ≥1 feature de travail) mais que sa clôture a été perdue — le PTY
    tué AVANT `verify_and_complete` (SIGHUP navigation d'onglet, Ctrl-C, crash) : le travail survit
    (worktree/DB), seule la clôture manque. Réserve le worktree (idempotent), VÉRIFIE et clôt (tasks socle
    `done`), commit le design.md. **Idempotent / re-jouable** : no-op propre s'il n'y a aucun socle interactif
    en attente, si le socle n'est pas encore travaillé, ou s'il est déjà clos. Retourne l'outcome de
    `verify_and_complete`, ou `None` s'il n'y a rien à réconcilier. Réutilisé par `run_interview` (reconcile-
    only avant lancement) ET l'orchestrateur (`forgemaster run` auto-heal — clôt hors survie du PTY)."""
    git = git or InternalGit()
    resolved = resolve_interview(conn, project)
    if resolved is None:
        return None                              # aucun socle interactif READY (déjà clos, ou pas de socle)
    feature, _task = resolved
    if not _socle_worked(conn, settings, project, feature):
        return None                              # pas encore objectivement travaillé → rien à clore
    res = worktree.reserve(conn, settings, git, project=project, feature=feature["slug"])
    outcome = verify_and_complete(conn, settings, project, feature, wt_path=res["path"])
    design_sha = _commit_design(git, project, feature, res["path"]) if outcome["completed"] else None
    return {**outcome, "design_sha": design_sha,
            "next_step": ("Drain des features de travail (`forgemaster run`)." if outcome["completed"]
                          else "Roadmap encore incomplète — termine l'interview.")}


def reconcile_socle_report(conn, settings: Settings, *, project: str,
                           git: GitBackend | None = None) -> dict:
    """Variante **rapportée** de `reconcile_socle` pour l'UI (« Valider l'interview & clôturer le socle ») :
    agit (clôt le socle si prêt) et rend TOUJOURS un compte-rendu humain — jamais `None`. `status` ∈
    {`reconciled` (socle clôturé, design committé), `already_closed` (rien à faire), `interview_incomplete`
    (il manque design/roadmap), `no_socle` (projet sans socle interactif)}. Chaque cas porte son `next_step`
    en clair. Délègue l'ACTION à `reconcile_socle` (idempotent) ; n'ajoute que la désambiguïsation du `None`
    pour ne rien afficher de muet (deep-linkable, robuste à une course état/clic)."""
    git = git or InternalGit()
    outcome = reconcile_socle(conn, settings, project=project, git=git)
    if outcome is not None:                          # socle travaillé → clôturé (completed toujours vrai ici)
        return {"status": "reconciled", "feature": (socle_feature(conn, project) or {}).get("slug"),
                **outcome}
    socle = socle_feature(conn, project)
    if socle is None:
        return {"status": "no_socle", "completed": False, "feature": None, "design_sha": None,
                "socle_tasks_closed": 0, "issues": [],
                "next_step": "Pas de socle d'interview pour ce projet."}
    if resolve_interview(conn, project) is None:     # socle présent mais plus de task interactive READY
        return {"status": "already_closed", "completed": True, "feature": socle["slug"], "design_sha": None,
                "socle_tasks_closed": 0, "issues": [],
                "next_step": "Socle déjà clôturé — draine les features de travail (`forgemaster run`)."}
    issues = [f"{i.kind} {i.feature}/{i.task or '-'}" for i in check.check_roadmap(conn, project)]
    why = ("aucune feature de travail authorée"
           if not _has_work_feature(conn, project, socle["slug"]) else "roadmap incomplète")
    return {"status": "interview_incomplete", "completed": False, "feature": socle["slug"],
            "design_sha": None, "socle_tasks_closed": 0, "issues": issues,
            "next_step": f"Termine l'interview ({why}) avant de clôturer le socle."}


def interview_env(settings: Settings) -> dict[str, str]:
    """Env de la session d'interview = `tools.cli_env` (tools_env + bin du venv forgemaster). La session
    AUTHORE
    la roadmap dans la DB via `forgemaster roadmap add-feature` (prescrit par `roadmap-decompose`) et lance
    `claude` (via `~/.local/bin`) : sans cet env, `env=None` héritait d'un PATH fragile sans `forgemaster` ni
    `node`, la session ne pouvait NI authorer NI vérifier → repli YAML que la réconciliation ne voit pas."""
    return cli_env(settings)


def _default_launcher(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    """Lance `claude` en HÉRITANT le terminal (stdin/stdout/stderr non capturés) → l'humain dialogue en
    direct. Retourne le code de sortie. Isolé (patchable en test), mais les tests injectent un launcher : ils
    ne passent jamais ici."""
    return subprocess.run(argv, cwd=str(cwd), env=env, check=False).returncode


def run_interview(conn, settings: Settings, *, project: str, git: GitBackend | None = None,
                  launcher: Launcher | None = None) -> dict:
    """Mène le cycle interview d'UN projet : résout la task interactive du socle, réserve son worktree, lance
    `claude` interactif seedé du prompt préparé, puis VÉRIFIE et clôt le socle. Retourne un rapport
    `{ran, reason, feature?, task?, completed?, issues?, reconciled?}`. **Gate no-interactive-no-launch** :
    `ran=False` (sans lancement) s'il n'y a aucune task interactive en attente. **Reconcile-only** : si le
    socle est DÉJÀ travaillé (interview antérieure interrompue avant clôture), clôt SANS relancer claude
    (`reason="reconcile-only"`). Sinon, chemin normal : lance l'interview puis vérifie/clôt à la sortie ; le
    worktree du socle est committé (la doc de design éditée) si l'interview a produit (feature de travail)."""
    git = git or InternalGit()
    resolved = resolve_interview(conn, project)
    if resolved is None:
        return {"ran": False, "reason": "aucune interview en attente (pas de task interactive READY)"}
    feature, task = resolved
    # Reconcile-only : une interview antérieure a produit mais sa clôture a été perdue (PTY tué) → clôt SANS
    # relancer claude (idempotent). Sinon la re-run relancerait l'interview sur un socle déjà travaillé.
    reconciled = reconcile_socle(conn, settings, project=project, git=git)
    if reconciled is not None:
        return {"ran": True, "reason": "reconcile-only", "feature": feature["slug"], "task": task["slug"],
                "completed": reconciled["completed"], "issues": reconciled["issues"],
                "work_feature": reconciled["work_feature"], "reconciled": True}
    # Chemin normal : socle neuf/incomplet → session interactive, puis clôture vérifiée à la sortie.
    # Confiance du workspace : le SoT bare du projet (comme le worker) — sinon `claude` ignore les allowed*.
    auth.trust_workspace(sot_path_for(settings, project))
    res = worktree.reserve(conn, settings, git, project=project, feature=feature["slug"])
    # Câble le MCP de corpus dans le worktree où `claude` interactif tourne (miroir du dispatch headless
    # `worker._run_worker`) → l'interview DÉCOUVRE le MCP (`/mcp`, solve-mode). No-op honnête si non câblé.
    inject_mcp_config(res["path"], settings, slug=project)
    prompt = build_interview_prompt(get_project(conn, project), feature, task)
    # Épingle l'id de session AVANT le lancement (v14) : `--session-id` rend le transcript déterministe et
    # persiste l'id même si la session est interrompue (PTY tué) → son coût token reste sommable après coup.
    session_id = ids.new_id()
    registry.record_interview_session(conn, project, session_id)
    argv = build_interview_argv(prompt, session_id=session_id)
    launch = launcher or _default_launcher
    launch(argv, cwd=res["path"], env=interview_env(settings))
    # Réconciliation / vérification à la sortie : la doc éditée + la roadmap authorée sont dans worktree/DB.
    outcome = verify_and_complete(conn, settings, project, feature, wt_path=res["path"])
    if outcome["completed"]:
        _commit_design(git, project, feature, res["path"])
    return {"ran": True, "reason": "ok", "feature": feature["slug"], "task": task["slug"],
            "completed": outcome["completed"], "issues": outcome["issues"],
            "work_feature": outcome["work_feature"], "reconciled": False}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `forgemaster interview <projet>` : lance l'interview terminale interactive du socle. **Gate
    d'auth**
    (comme le dispatch) : refuse AVANT tout lancement si la machine n'a pas d'auth Claude explicite."""
    if not auth.claude_auth_status()["authenticated"]:
        print(f"erreur : {auth.AUTH_HINT}")
        return 2
    conn = store.open_db(settings)
    try:
        report = run_interview(conn, settings, project=args.project)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    if not report["ran"]:
        print(report["reason"])
        return 1
    if report["completed"]:
        print(f"interview {args.project}/{report['feature']} terminée — socle clôturé "
              f"(roadmap check vert + feature de travail). `forgemaster run {args.project}` peut drainer.")
        return 0
    fallback = "aucune feature de travail authorée" if not report["work_feature"] else "roadmap incomplète"
    why = "; ".join(report["issues"][:3]) or fallback
    print(f"interview {args.project}/{report['feature']} incomplète — socle laissé ouvert : {why}. "
          f"Relance `forgemaster interview {args.project}` pour reprendre.")
    return 1
