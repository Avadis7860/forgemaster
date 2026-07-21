"""routes/templates — router de domaine « vitrine des templates de référence UI » : LISTER en lecture les
templates de référence embarqués (les dossiers zéro-build sous `web/public/templates/<slug>/`, copiés verbatim
dans le build SPA par Vite, donc servis à `/templates/<slug>/…` par le catch-all de `daemon.app`).

Pendant que `routes/bundles` introspecte ce que le cockpit **sème** (bundles vendorés) et `routes/capital` ce
qu'il **loue** (corpus MCP), ce router expose le **capital-token montrable** : des modèles d'UI qu'une session
peut appliquer à un projet. Le format d'un template (dossier + `template.toml` sec) est posé par la mission
`cockpit-ui-template-socle-format`.

Read-only, idempotent, goto-safe : un seul `GET /api/templates` scanne le répertoire statique servi et parse
chaque `template.toml`. Aucune donnée n'est mutée, aucun input utilisateur n'atteint le FS (on n'itère que les
noms de dossiers présents → aucun path-traversal possible, comme `bundles`).

Fail-closed **silencieux** : un dossier sans `template.toml`, ou avec un TOML illisible / sans table
`[template]`, est **ignoré** (jamais un 500) — la vitrine ne montre que ce qu'elle sait décrire.
`web_dist_dir()` absent (dev sans build) → liste vide honnête. La preview (`preview.png`) et l'entrée
(`index.html`) ne sont PAS servies ici : le front les charge en statique (`<img>` / `<iframe>`) via le
catch-all, sous `/templates/<slug>/`.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from fastapi import APIRouter

from cockpit.daemon.app import web_dist_dir

# Sous-dossier (relatif à la racine statique servie) où vivent les templates de référence — miroir de
# `web/public/templates/` copié verbatim dans `web/dist/templates/` par Vite (publicDir).
_TEMPLATES_SUBDIR = "templates"


def _one(slug: str, meta: dict) -> dict:
    """Projette la table `[template]` d'un `template.toml` en résumé de vitrine. Le **slug canonique = le nom
    de dossier** (source d'URL sûre) ; on ne fait pas confiance au `slug` du TOML pour construire un chemin.
    Les champs absents dégradent en défaut sec (`""` / `[]`) plutôt que d'exclure le template."""
    return {
        "slug": slug,
        "name": str(meta.get("name") or slug),
        "tool_type": str(meta.get("tool_type") or ""),
        "genre": str(meta.get("genre") or ""),
        "tags": [str(t) for t in meta.get("tags", []) if isinstance(t, str)],
        "intention": str(meta.get("intention") or ""),
        "preview": str(meta.get("preview") or "preview.png"),
        "entry": str(meta.get("entry") or "index.html"),
        "themes": [str(t) for t in meta.get("themes", []) if isinstance(t, str)],
    }


def _scan(root: Path) -> list[dict]:
    """Scanne `root/templates/*/template.toml`, trié par slug. Chaque dossier décrit par une table
    `[template]` valide devient un résumé ; toute erreur (dossier sans TOML, TOML cassé, pas de `[template]`)
    **exclut ce dossier** sans faire échouer le scan (fail-closed silencieux)."""
    base = root / _TEMPLATES_SUBDIR
    if not base.is_dir():
        return []
    out: list[dict] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "template.toml"
        if not manifest.is_file():
            continue
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        meta = data.get("template")
        if not isinstance(meta, dict):
            continue
        out.append(_one(child.name, meta))
    return out


def make_templates_router() -> APIRouter:
    router = APIRouter(prefix="/api/templates", tags=["templates"])

    @router.get("")
    def list_templates() -> dict:
        """La **vitrine** : liste les templates de référence servis (nom, tool_type/genre, tags, intention,
        preview, entry, thèmes). Source = `web_dist_dir()/templates/*/template.toml` (le build SPA effectif).
        GET idempotent, goto-safe. Vide honnête si aucun template (dev sans build) — jamais d'erreur."""
        return {"templates": _scan(web_dist_dir())}

    return router
