"""design.seed — écrit la GRAINE d'inspiration d'un template UI appliqué dans le worktree d'un projet :
`<worktree>/docs/design/<slug>/{brief.md, tokens.css, preview.png}`.

Symétrique de `dispatch.worker.write_decision_doc` (écriture in-project d'un capital que
`roadmap.prompt._design_block` relit — côté ÉCRITURE ici, côté LECTURE là). Purement filesystem : aucune
horloge ni réseau (le `brief.md` est passé tout construit par `design.apply`). Les fichiers frères (identité
`tokens.css`, rendu-cible `preview.png`) sont copiés verbatim depuis le dossier du template servi."""
from __future__ import annotations

import shutil
from pathlib import Path

# Fichiers frères copiés verbatim depuis le dossier du template servi. Absents à la source ⇒ simplement non
# copiés (fail-soft : un template minimal peut n'avoir que son brief). Le worker les lit au besoin ; le brief
# (relu par `_design_block`) les pointe.
_SIBLINGS = ("tokens.css", "preview.png")


def write_design_seed(worktree: Path, template_slug: str, *, source_dir: Path,
                      brief_text: str) -> Path | None:
    """Pose la graine d'un template appliqué sous `<worktree>/docs/design/<template_slug>/` : le `brief.md`
    (cible visuelle + consigne « inspire-toi, customise ») et les fichiers frères présents (`tokens.css`,
    `preview.png`) copiés depuis `source_dir` (le dossier du template servi). **No-op** (retourne `None`, rien
    écrit) si `brief_text` est blanc : pas de graine vide (symétrie avec `write_decision_doc`). Retourne le
    chemin du `brief.md` écrit. Déterministe (aucune horloge — le brief est construit par l'appelant)."""
    if not brief_text or not brief_text.strip():
        return None
    dest = Path(worktree) / "docs" / "design" / template_slug
    dest.mkdir(parents=True, exist_ok=True)
    brief = dest / "brief.md"
    brief.write_text(brief_text if brief_text.endswith("\n") else brief_text + "\n", encoding="utf-8")
    for name in _SIBLINGS:
        src = Path(source_dir) / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    return brief
