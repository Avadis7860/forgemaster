"""catalog — requêtes de design-system sur l'index front-map d'un projet.

Consomme le CLI `frontmap` en **boîte-noire** (subprocess via le seam `core.run`) : `tokens` (design tokens),
`primitives` (catalogue de composants), `routes` (arbre des routes). Le contrat JSON est figé côté front-map —
chaque verbe imprime `{ "<clé>": [...], "count": N, "engine": ... }` (relayé tel quel). L'index est garanti
frais par `ensure_index` (cache SHA+version). Jumeau de `forgemaster.codemap.flow`, sans `--format json` (la
sortie front-map est déjà du JSON) ni graphe (c'est un catalogue, pas un flot).
"""
from __future__ import annotations

import json
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.core.run import RunResult, run
from forgemaster.frontmap.index import FrontmapError, Runner, ensure_index, frontmap_argv

QUERY_TIMEOUT_S = 60            # lecture jsonl + agrégation : rapide ; borné par sûreté.


def _catalog(
    verb: str, settings: Settings, project: str, sot: Path, *, ref: str, runner: Runner,
) -> dict:
    """Rejoue un verbe catalogue front-map (`tokens`/`primitives`/`routes`) sur l'index frais du projet et
    relaie son JSON. Bâtit l'index si nécessaire (cache SHA+version)."""
    handle = ensure_index(settings, project, sot, ref=ref, runner=runner)
    res = runner(frontmap_argv(verb, "--root", str(handle.root)), timeout=QUERY_TIMEOUT_S)
    return _parse_json(res, verb)


def tokens(
    settings: Settings, project: str, sot: Path, *, ref: str = "dev", runner: Runner = run,
) -> dict:
    """Design tokens indexés : `{tokens:[{name,value,group,source_file,line,lead}], count, engine}`."""
    return _catalog("tokens", settings, project, sot, ref=ref, runner=runner)


def primitives(
    settings: Settings, project: str, sot: Path, *, ref: str = "dev", runner: Runner = run,
) -> dict:
    """Catalogue de primitives : `{primitives:[{name,file,line,props,variants,defaults,lead}], count, …}`."""
    return _catalog("primitives", settings, project, sot, ref=ref, runner=runner)


def routes(
    settings: Settings, project: str, sot: Path, *, ref: str = "dev", runner: Runner = run,
) -> dict:
    """Arbre des routes du front : `{routes:[{var,path,full_path,component,parent,is_root,file,line}], …}`."""
    return _catalog("routes", settings, project, sot, ref=ref, runner=runner)


def _parse_json(res: RunResult, label: str) -> dict:
    """Parse la sortie JSON du CLI. Un stdout illisible → `FrontmapError` (on remonte stderr, jamais un
    demi-résultat inventé)."""
    try:
        parsed = json.loads(res.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise FrontmapError(
            f"sortie `frontmap {label}` illisible : {res.stderr.strip()[:200]}") from exc
    if not isinstance(parsed, dict):
        raise FrontmapError(f"sortie `frontmap {label}` inattendue (pas un objet JSON)")
    return parsed
