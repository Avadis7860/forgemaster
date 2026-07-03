"""flow — requêtes de flot d'exécution sur l'index code-map d'un projet.

Consomme le CLI `codemap flow` en **boîte-noire** (subprocess via le seam `core.run`) : `--list` (opérations
découvertes : routes API + verbes CLI) et `<op> --format json` (sous-graphe borné). Le contrat JSON est
figé côté code-map — `list_operations` renvoie `{operations:[{operation,entry,kind}], engine}`, `flow`
renvoie `{ok, operation, entry, nodes[], edges[], stats{indirect_ratio, …}}` (relayé tel quel, `ok:false`
compris pour une opération introuvable/ambiguë). L'index est garanti frais par `ensure_index` (cache SHA).
"""
from __future__ import annotations

import json
from pathlib import Path

from cockpit.codemap.index import CodemapError, Runner, codemap_argv, ensure_index
from cockpit.config import Settings
from cockpit.core.run import RunResult, run

DEFAULT_DEPTH = 6
FLOW_TIMEOUT_S = 60             # lecture jsonl + BFS borné : rapide ; borné par sûreté.


def list_operations(
    settings: Settings, project: str, sot: Path, *, ref: str = "dev", runner: Runner = run,
) -> dict:
    """Opérations (entry points) découvertes dans le projet : `{operations:[{operation,entry,kind}],
    engine}`. Bâtit l'index si nécessaire (cache SHA). Lève `CodemapError` si le build échoue ou la sortie
    est illisible."""
    handle = ensure_index(settings, project, sot, ref=ref, runner=runner)
    res = runner(codemap_argv("flow", "--list", "--root", str(handle.root)), timeout=FLOW_TIMEOUT_S)
    return _parse_json(res, "flow --list")


def flow(
    settings: Settings, project: str, sot: Path, *, operation: str, ref: str = "dev",
    depth: int = DEFAULT_DEPTH, runner: Runner = run,
) -> dict:
    """Sous-graphe de flot d'une opération : `{ok, operation, entry, nodes[], edges[], stats}`. `ok:false`
    (opération introuvable/ambiguë) est relayé tel quel — l'appelant décide de son rendu. Bâtit l'index si
    nécessaire (cache SHA)."""
    handle = ensure_index(settings, project, sot, ref=ref, runner=runner)
    res = runner(
        codemap_argv("flow", operation, "--format", "json", "--depth", str(depth),
                     "--root", str(handle.root)),
        timeout=FLOW_TIMEOUT_S,
    )
    return _parse_json(res, f"flow {operation}")


def _parse_json(res: RunResult, label: str) -> dict:
    """Parse la sortie JSON du CLI (`--format json` sort rc 0 même sur `ok:false`). Un stdout illisible →
    `CodemapError` (on remonte stderr, jamais un demi-résultat inventé)."""
    try:
        parsed = json.loads(res.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise CodemapError(
            f"sortie `codemap {label}` illisible : {res.stderr.strip()[:200]}") from exc
    if not isinstance(parsed, dict):
        raise CodemapError(f"sortie `codemap {label}` inattendue (pas un objet JSON)")
    return parsed
