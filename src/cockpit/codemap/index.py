"""index — matérialisation + build de l'index code-map d'un projet, CACHÉ par SHA.

Un projet est un SoT git **bare** + worktrees **éphémères** : aucun checkout source stable à cartographier.
On matérialise donc l'arbre d'une réf (défaut `dev`) via `InternalGit.archive` dans un cache dérivé sous
`settings.home/"codemap"/<projet>/<sha>/`, puis on y lance `codemap build` (subprocess, seam `core.run`).

Fraîcheur **SHA-bound** (même convention que les verdicts gate, cf. `gate/toolchain.state_path`) : un SHA =
un dossier ; l'index n'est reconstruit que si le SHA change (cache hit si le marqueur `.codemap/…` est là).
code-map est consommé en **boîte-noire CLI** (son identité : outil-carte réutilisable hors cockpit) — aucun
import de son API Python interne. Le `runner` (seam `core.run`) est injectable pour le test.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cockpit.config import Settings
from cockpit.core.run import RunResult, run
from cockpit.git.internal import GitOpError, InternalGit

Runner = Callable[..., RunResult]

BUILD_TIMEOUT_S = 300           # `codemap build` (stdlib-pur) : quelques secondes ; borné pour ne pas pendre.
_INDEX_MARKER = ".codemap/calls.manifest.json"  # présence = index bâti pour ce SHA (cache hit).


def codemap_argv(*args: str) -> list[str]:
    """Argv d'invocation de code-map **via le python courant** (`sys.executable -m codemap`) — jamais
    `["codemap", …]` : le service systemd de prod n'a pas le `.venv/bin` sur son PATH, un lookup nu
    échouerait. `-m codemap` utilise le venv du cockpit (où code-map est installé), sans dépendance PATH."""
    return [sys.executable, "-m", "codemap", *args]


class CodemapError(RuntimeError):
    """Échec de matérialisation ou de build de l'index code-map d'un projet (message porté à l'appelant)."""


@dataclass(frozen=True)
class IndexHandle:
    """Index code-map matérialisé pour un `(projet, réf, sha)`. `root` = racine de l'arbre extrait (contient
    `.codemap/`), à passer en `--root` aux requêtes `codemap flow`."""

    project: str
    ref: str
    sha: str
    root: Path


def index_dir_for(settings: Settings, project: str, sha: str) -> Path:
    """Dossier de cache dérivé pour l'index d'un `(projet, sha)` : `home/codemap/<projet>/<sha>` (convention
    identique à `gate/toolchain.state_path`, invalidée par SHA)."""
    return settings.home / "codemap" / project / sha


def ensure_index(
    settings: Settings, project: str, sot: Path, *, ref: str = "dev", runner: Runner = run,
) -> IndexHandle:
    """Garantit un index code-map frais pour `(project, ref)` et renvoie son `IndexHandle`.

    Cache hit si le dossier du SHA courant porte déjà `.codemap/calls.manifest.json` (rien à faire). Sinon :
    matérialise l'arbre (`git archive <sha>`) puis `codemap build --root`. `sot` est le SoT bare du projet
    (résolu par l'appelant). Lève `CodemapError` (réf absente, archive ou build en échec)."""
    git = InternalGit()
    try:
        sha = git.feature_sha(sot, ref)         # rev-parse <ref> → SHA plein (clé de cache)
    except GitOpError as exc:
        raise CodemapError(f"réf introuvable ({ref}) pour {project} : {exc}") from exc

    root = index_dir_for(settings, project, sha)
    if (root / _INDEX_MARKER).is_file():        # cache hit : index déjà bâti pour ce SHA
        return IndexHandle(project=project, ref=ref, sha=sha, root=root)

    root.mkdir(parents=True, exist_ok=True)
    try:
        git.archive(sot, sha, root)             # matérialise l'arbre du SHA (déterministe, immuable)
    except GitOpError as exc:
        raise CodemapError(f"matérialisation impossible ({project}@{ref}) : {exc}") from exc

    res = runner(codemap_argv("build", "--root", str(root)), cwd=root, timeout=BUILD_TIMEOUT_S)
    if not res.ok or not (root / _INDEX_MARKER).is_file():
        raise CodemapError(
            f"`codemap build` a échoué ({project}@{ref}) : {res.stderr.strip()[:200]}")
    return IndexHandle(project=project, ref=ref, sha=sha, root=root)
