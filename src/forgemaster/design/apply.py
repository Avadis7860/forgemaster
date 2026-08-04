"""design.apply — applique un template UI de référence à un projet (« inspire mon projet de ce template »).

Forme A (crée le TRAVAIL de customisation, choix bosse 2026-07-23) : `apply_template` (a) crée la feature
`design-<slug>` + une task `customize-ui` (le worker touchera le vrai `web/` → merge = revue UI normale, pas
l'impasse « feature sans task » du gate) ; (b) réserve son worktree ; (c) y écrit + committe la graine
`docs/design/<slug>/` (`seed.write_design_seed`). Le worker de customisation relit le brief
(`roadmap.prompt._design_block`) et customise. **Idempotent** (ré-inspiration : feature/task déjà là →
réutilise ; commit no-op sur arbre net). Cœur **déterministe**, `git` injectable — aucun `claude` spawné,
donc pas de gate d'auth (cf. `routes/dispatch` `abort`/`reconcile-socle`). Le merge reste **human-GO**."""
from __future__ import annotations

import sqlite3
import tomllib
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.design.seed import write_design_seed
from forgemaster.dispatch import worktree
from forgemaster.git.backend import GitBackend
from forgemaster.git.identity import resolve_identity
from forgemaster.git.internal import InternalGit
from forgemaster.projects.registry import get_project
from forgemaster.roadmap import model

DESIGN_FEATURE_PREFIX = "design-"
CUSTOMIZE_TASK_SLUG = "customize-ui"


def _read_template_meta(source_dir: Path) -> dict:
    """Table `[template]` du `template.toml` du dossier servi (nom, intention, thèmes). Absent/cassé ⇒ `{}`
    (fail-soft : le brief dégrade en défaut sec). Miroir de `routes/templates._scan` sur un seul dossier."""
    manifest = Path(source_dir) / "template.toml"
    if not manifest.is_file():
        return {}
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    meta = data.get("template")
    return meta if isinstance(meta, dict) else {}


def _build_brief(project: str, template_slug: str, meta: dict) -> str:
    """`brief.md` **déterministe** d'un template appliqué : l'intention (cible visuelle), la consigne
    d'inspiration+customisation, les pointeurs vers les frères. Relu par `prompt._design_block`. PUR."""
    name = str(meta.get("name") or template_slug)
    intention = str(meta.get("intention") or "").strip()
    themes = [str(t) for t in meta.get("themes", []) if isinstance(t, str)]
    themes_line = (f"\n- **Colorimétries** (portées par `tokens.css`) : {', '.join(themes)}."
                   if themes else "")
    return (
        f"# Cible visuelle — {name}\n\n"
        f"Le dirigeant a appliqué le template de référence **`{template_slug}`** au projet **{project}** "
        f"comme cible d'inspiration pour l'UI.\n\n"
        f"## Intention\n{intention or '(sans intention déclarée)'}\n\n"
        f"## Consigne\n"
        f"Inspire-toi de cette référence : reprends son **identité** (les design-tokens de `tokens.css` — "
        f"couleurs, glow, radius…) et sa **structure**, puis **customise-les au projet** (nommage, contenu, "
        f"contraintes propres). Ce n'est PAS une copie verbatim : c'est un point de départ « voici à quoi ça "
        f"doit ressembler ».{themes_line}\n\n"
        f"## Fichiers de la graine (ce dossier)\n"
        f"- `tokens.css` — l'identité (design-tokens), ton point de départ à adapter.\n"
        f"- `preview.png` — le rendu-cible at-rest (regarde-le).\n"
    )


def apply_template(conn: sqlite3.Connection, settings: Settings, git: GitBackend | None = None, *,
                   project: str, template_slug: str, source_dir: Path) -> dict:
    """Applique le template `template_slug` (dont le dossier servi est `source_dir`) au projet `project`.
    Crée (idempotent) la feature+task de customisation, réserve le worktree, sème + committe la graine
    `docs/design/<slug>/`. Retourne `{project, template, feature, task, branch, path, commit, files}`. Lève
    `KeyError` si le projet est absent (→ 404) ; `ValueError` si le template est introuvable (→ 400)."""
    git = git or InternalGit()
    source = Path(source_dir)
    if not source.is_dir():
        raise ValueError(f"template introuvable : {template_slug!r} (dossier servi absent)")
    get_project(conn, project)                      # KeyError si projet absent (→ 404 handler global)
    feature_slug = f"{DESIGN_FEATURE_PREFIX}{template_slug}"
    feature_ref = f"{project}/{feature_slug}"

    # (a) feature de customisation — idempotent (ré-inspiration réutilise la même feature)
    if feature_slug not in {f["slug"] for f in model.list_features(conn, project)}:
        model.add_feature(conn, project_slug=project, slug=feature_slug,
                          title=f"Customiser l'UI depuis le template {template_slug}")
    # (b) task de customisation — idempotent
    feat = model.resolve_feature(conn, feature_ref)
    if CUSTOMIZE_TASK_SLUG not in {t["slug"] for t in model.list_tasks(conn, feat["id"])}:
        meta_for_acc = _read_template_meta(source)
        acceptance = (
            f"Customise l'UI de {project} en t'inspirant du template « {template_slug} » "
            f"({meta_for_acc.get('name') or template_slug}) : reprends son identité (design-tokens) et sa "
            f"structure, adapte-les au projet (ne copie pas verbatim). "
            f"Réf dans le repo : docs/design/{template_slug}/ (brief.md + tokens.css + preview.png).")
        model.add_task(conn, feature_ref=feature_ref, slug=CUSTOMIZE_TASK_SLUG,
                       title=f"Customiser l'UI ({template_slug})", acceptance=acceptance, priority="P2")

    # (c) réserve le worktree, sème + committe la graine (la forge committe ; jamais le worker)
    res = worktree.reserve(conn, settings, git, project=project, feature=feature_slug)
    brief = _build_brief(project, template_slug, _read_template_meta(source))
    written = write_design_seed(res["path"], template_slug, source_dir=source, brief_text=brief)
    commit = git.commit_worktree(
        res["path"], message=f"docs(design): applique le template {template_slug} (cible visuelle)",
        identity=resolve_identity(project, worktree.WORKTREE_BASE, role="worker"))
    seed_dir = Path(res["path"]) / "docs" / "design" / template_slug
    files = sorted(p.name for p in seed_dir.iterdir()) if written else []
    return {"project": project, "template": template_slug, "feature": feature_slug,
            "task": CUSTOMIZE_TASK_SLUG, "branch": res["branch"], "path": str(res["path"]),
            "commit": commit, "files": files}
