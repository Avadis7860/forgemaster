"""facet — résolution et activation de la facette de dispatch d'une feature dans sa worktree.

Une facette **aligne** le worker sur le type de travail de sa feature (backend / frontend / tool / doc).
Deux canaux, stricts :
- **persona + méthode** → injectées dans le PROMPT (lues depuis les `.md` committés `.claude/facets/<f>/`
  présents dans la worktree ; cf. `roadmap/prompt.py`) — zéro fichier posé ;
- **hooks + permissions** → un fichier `.claude/settings.local.json` **GITIGNORÉ** écrit dans la worktree
  au dispatch. Les worktrees **partagent l'arbre committé** → la seule voie de différenciation par-feature
  est un fichier local non versionné (copié depuis la source committée `.claude/facets/<f>/`).

Déterministe (lecture locale, zéro réseau, zéro LLM), fail-soft (bundle/facette absents → dégradation propre).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

_FALLBACK_FACET = "doc"   # défaut ultime si ni feature.facet ni bundle.toml ne tranchent


def facet_dir(root: Path, facet: str) -> Path:
    """Dossier source committé d'une facette dans un repo/worktree : `<root>/.claude/facets/<facet>/`."""
    return Path(root) / ".claude" / "facets" / facet


def resolve_facet(root: Path, feature_facet: str | None) -> str:
    """La facette **effective** d'une feature : `feature_facet` s'il est posé, sinon le `default_facet` du
    `.cockpit/bundle.toml` de la worktree, sinon `doc`. Fail-soft (manifeste absent/illisible → fallback)."""
    if feature_facet:
        return feature_facet
    manifest = Path(root) / ".cockpit" / "bundle.toml"
    if manifest.is_file():
        try:
            bundle = tomllib.loads(manifest.read_text(encoding="utf-8")).get("bundle", {})
        except (tomllib.TOMLDecodeError, OSError):
            bundle = {}
        default = bundle.get("default_facet")
        if isinstance(default, str) and default:
            return default
    return _FALLBACK_FACET


def activate_facet(wt: Path, facet: str) -> str | None:
    """Active la facette dans la worktree : copie `.claude/facets/<facet>/settings.local.json` (source
    committée) → `.claude/settings.local.json` (GITIGNORÉ, lu par `claude -p`). Idempotent (overwrite).
    Retourne le chemin écrit, ou None si la facette n'a pas de `settings.local.json` (fail-soft)."""
    src = facet_dir(wt, facet) / "settings.local.json"
    if not src.is_file():
        return None
    dst = Path(wt) / ".claude" / "settings.local.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(dst)
