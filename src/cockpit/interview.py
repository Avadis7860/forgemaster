"""interview — point d'entrée **terminal interactif** de la 1ʳᵉ session d'un projet neuf.

Le dispatch (`worker.dispatch_next`) refuse le spawn headless d'une task `mode=interactive` et la surface en
`needs_interview` : une interview / un cadrage ne se mène pas en `claude -p` (aucun interlocuteur). CE module
est le pendant humain — `cockpit interview <projet>` lance un `claude` **interactif** (TTY hérité, jamais
`-p`) dans le worktree du socle, seedé d'un prompt préparé qui **pointe le skill semé**
`first-session-interview` (la méthode design-first vit dans le bundle, PAS dans ce moteur générique —
invariant « le socle porte le hint »). L'humain mène l'interview, remplit la doc de design, puis AUTHORE la
roadmap de travail (`roadmap-decompose` → `cockpit roadmap add-feature`). À la sortie, la forge **vérifie**
(roadmap check vert + ≥1 feature de travail) et clôt le socle — verified, pas trusted.

Transport injectable (`Launcher`) : les tests ne lancent JAMAIS un vrai `claude`. Zéro I/O réseau au build
du prompt (déterministe, comme `roadmap/prompt.py`).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from cockpit import auth
from cockpit.config import Settings
from cockpit.db import store
from cockpit.dispatch import worktree
from cockpit.git.backend import GitBackend
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import InternalGit
from cockpit.projects.registry import get_project, sot_path_for
from cockpit.roadmap import check, model, resolver
from cockpit.tools import tools_env

# launcher(argv, *, cwd, env) -> returncode. Défaut = subprocess TTY hérité ; injecté en test (jamais un
# vrai `claude`). Distinct du `Runner` du worker (qui CAPTURE le stdout `-p`) : ici stdout/stdin/stderr sont
# HÉRITÉS (l'humain parle à `claude` dans son terminal), rien n'est capturé.
Launcher = Callable[..., int]


def build_interview_argv(prompt: str, *, model_name: str | None = None) -> list[str]:
    """Argv d'un `claude` **interactif** (jamais `-p`) seedé du `prompt` comme premier message. Interactif par
    défaut (le TTY est hérité au lancement). PUR."""
    argv = ["claude"]
    if model_name:
        argv += ["--model", model_name]
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
        f"`cockpit roadmap add-feature {slug} <feature> --facet <f> …` puis "
        f"`cockpit task add {slug}/<feature> <task> --acceptance \"…\"`, jusqu'à "
        f"`cockpit roadmap check {slug}` vert avec ≥1 feature de travail.\n"
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


def _has_work_feature(conn, project: str, socle_slug: str) -> bool:
    """≥1 feature de travail existe = au moins une feature AUTRE que le socle (un projet neuf n'a que le socle
    semé ; `roadmap-decompose` en ajoute). C'est le critère binaire objectif de « l'interview a produit »."""
    return any(f["slug"] != socle_slug for f in model.list_features(conn, project))


def verify_and_complete(conn, project: str, feature: dict) -> dict:
    """VÉRIFIE le socle après la session (verified, pas trusted) : `roadmap check` vert ET ≥1 feature de
    travail → clôt TOUTES les tasks du socle en `done` (l'interview a rempli la doc, decompose a authoré la
    roadmap). Sinon laisse le socle `todo` et rend ce qui manque. Rend `{completed, issues, work_feature}`."""
    issues = check.check_roadmap(conn, project)
    work = _has_work_feature(conn, project, feature["slug"])
    completed = not issues and work
    if completed:
        conn.execute("UPDATE tasks SET status = 'done' WHERE feature_id = ? AND status != 'cancelled'",
                     (feature["id"],))
        conn.commit()
    return {"completed": completed, "issues": [f"{i.kind} {i.feature}/{i.task or '-'}" for i in issues],
            "work_feature": work}


def interview_env(settings: Settings) -> dict[str, str]:
    """Env de la session d'interview : `tools_env` (maps + Node + `~/.local/bin` pour `claude`, comme le
    worker) PRÉFIXÉ du bin du venv COURANT — là où vit `cockpit`. Car la session AUTHORE la roadmap dans la DB
    via `cockpit roadmap add-feature`/`cockpit task add` (prescrit par le skill `roadmap-decompose`), et
    `cockpit` n'est PAS dans `tools/bin`. `env=None` héritait d'un PATH fragile (shell lanceur / PATH systemd
    minimal du daemon) où `cockpit` — ET `node` — pouvaient manquer : la session ne pouvait alors NI authorer
    NI vérifier, elle repliait sur une édition YAML que la réconciliation (qui lit la DB) ne voit pas. PUR."""
    env = tools_env(settings)
    cockpit_bin = str(Path(sys.executable).parent)          # /…/venv/bin — porte le script `cockpit`
    env["PATH"] = os.pathsep.join([cockpit_bin, env["PATH"]])
    return env


def _default_launcher(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    """Lance `claude` en HÉRITANT le terminal (stdin/stdout/stderr non capturés) → l'humain dialogue en
    direct. Retourne le code de sortie. Isolé (patchable en test), mais les tests injectent un launcher : ils
    ne passent jamais ici."""
    return subprocess.run(argv, cwd=str(cwd), env=env, check=False).returncode


def run_interview(conn, settings: Settings, *, project: str, git: GitBackend | None = None,
                  launcher: Launcher | None = None) -> dict:
    """Mène le cycle interview d'UN projet : résout la task interactive du socle, réserve son worktree, lance
    `claude` interactif seedé du prompt préparé, puis VÉRIFIE et clôt le socle. Retourne un rapport
    `{ran, reason, feature?, task?, completed?, issues?}`. **Gate no-interactive-no-launch** : `ran=False`
    (sans lancement) s'il n'y a aucune task interactive en attente. Le worktree du socle est committé (la doc
    de design éditée) si l'interview a produit (feature de travail)."""
    git = git or InternalGit()
    resolved = resolve_interview(conn, project)
    if resolved is None:
        return {"ran": False, "reason": "aucune interview en attente (pas de task interactive READY)"}
    feature, task = resolved
    # Confiance du workspace : le SoT bare du projet (comme le worker) — sinon `claude` ignore les allowed*.
    auth.trust_workspace(sot_path_for(settings, project))
    res = worktree.reserve(conn, settings, git, project=project, feature=feature["slug"])
    prompt = build_interview_prompt(get_project(conn, project), feature, task)
    argv = build_interview_argv(prompt)
    launch = launcher or _default_launcher
    launch(argv, cwd=res["path"], env=interview_env(settings))
    # Réconciliation / vérification à la sortie : la doc éditée + la roadmap authorée sont dans worktree/DB.
    outcome = verify_and_complete(conn, project, feature)
    if outcome["completed"]:
        # La doc de design éditée devient durable sur la branche du socle (arbre net → no-op propre).
        git.commit_worktree(res["path"], message=f"docs({feature['slug']}): interview de 1ʳᵉ session",
                            identity=resolve_identity(project, worktree.WORKTREE_BASE, role="worker"))
    return {"ran": True, "reason": "ok", "feature": feature["slug"], "task": task["slug"],
            "completed": outcome["completed"], "issues": outcome["issues"],
            "work_feature": outcome["work_feature"]}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit interview <projet>` : lance l'interview terminale interactive du socle. **Gate d'auth**
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
              f"(roadmap check vert + feature de travail). `cockpit run {args.project}` peut drainer.")
        return 0
    fallback = "aucune feature de travail authorée" if not report["work_feature"] else "roadmap incomplète"
    why = "; ".join(report["issues"][:3]) or fallback
    print(f"interview {args.project}/{report['feature']} incomplète — socle laissé ouvert : {why}. "
          f"Relance `cockpit interview {args.project}` pour reprendre.")
    return 1
