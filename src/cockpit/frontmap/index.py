"""index — matérialisation + build de l'index front-map d'un projet, CACHÉ par (SHA, version d'outil).

Jumeau strict de `cockpit.codemap.index` (mêmes invariants : SoT bare + worktrees éphémères → aucun checkout
source stable ; on matérialise l'arbre d'une réf via `InternalGit.archive` dans un cache dérivé sous
`settings.home/"frontmap"/<projet>/<sha>/<version>/`, puis `frontmap build --root`). Deux écarts assumés vs
code-map :

- **Négociation de version** : front-map n'a pas `--schema-version` (constante de contrat) — on lit
  `--version` (`frontmap X.Y.Z`) comme moitié « outil » de la clé. Un upgrade ouvre un dossier de cache neuf,
  l'ancien index n'est jamais servi périmé. La négociation se fait **sans index** (le CLI imprime et sort).
- **Invocation** : `sys.executable -m frontmap …` (jamais `["frontmap", …]` — le service prod n'a pas le
  `.venv/bin` sur son PATH). `-m frontmap` exige le `__main__` de front-map (livré côté front-map, parité
  code-map). front-map reste consommé en **boîte-noire CLI** : on dépend de son contrat (rc, `--version`,
  stdout JSON), jamais de son arborescence interne. Le `runner` (seam `core.run`) est injectable pour le test.
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

BUILD_TIMEOUT_S = 300           # `frontmap build` (extraction tokens/tsx) : quelques secondes ; borné.
VERSION_TIMEOUT_S = 30          # `frontmap --version` : imprime une constante et sort ; borné.
_BUILT_MARKER = ".cockpit-frontmap-built"  # marqueur PROPRE au cockpit (découplé du layout front-map).


def frontmap_argv(*args: str) -> list[str]:
    """Argv d'invocation de front-map **via le python courant** (`sys.executable -m frontmap`) — jamais
    `["frontmap", …]` : le service systemd de prod n'a pas le `.venv/bin` sur son PATH, un lookup nu
    échouerait. `-m frontmap` utilise le venv du cockpit (où front-map est installé), sans dépendance PATH."""
    return [sys.executable, "-m", "frontmap", *args]


class FrontmapError(RuntimeError):
    """Échec de matérialisation ou de build de l'index front-map d'un projet (message porté à l'appelant)."""


@dataclass(frozen=True)
class IndexHandle:
    """Index front-map matérialisé pour un `(projet, réf, sha)`. `root` = racine de l'arbre extrait (contient
    `.frontmap/`), à passer en `--root` aux requêtes `frontmap tokens|primitives|routes`."""

    project: str
    ref: str
    sha: str
    root: Path


def frontmap_version(runner: Runner = run) -> str:
    """Version de l'OUTIL front-map (`frontmap --version` → `frontmap X.Y.Z`). **Sans index** : sert à câbler
    la clé de cache et forcer un rebuild à l'upgrade de l'outil, AVANT tout build. Non mémoïsé (coût
    négligeable hors boucle chaude). Lève `FrontmapError` si le CLI ne rend pas une version lisible."""
    res = runner(frontmap_argv("--version"), timeout=VERSION_TIMEOUT_S)
    version = res.stdout.strip()
    if not res.ok or not version:
        raise FrontmapError(f"`frontmap --version` illisible : {res.stderr.strip()[:200]}")
    return version.replace(" ", "-")            # "frontmap 0.1.0" → "frontmap-0.1.0" (segment de chemin sûr)


def index_dir_for(settings: Settings, project: str, sha: str, version: str) -> Path:
    """Dossier de cache dérivé pour l'index d'un `(projet, sha, version)` :
    `home/frontmap/<projet>/<sha>/<version>`. Invalidé par SHA (nouveau code) **ET** par `version` (upgrade de
    front-map) — un bump d'outil ouvre un dossier neuf, l'ancien index n'est jamais servi périmé."""
    return settings.home / "frontmap" / project / sha / version


def ensure_index(
    settings: Settings, project: str, sot: Path, *, ref: str = "dev", runner: Runner = run,
) -> IndexHandle:
    """Garantit un index front-map frais pour `(project, ref)` et renvoie son `IndexHandle`.

    Cache hit si le dossier `(sha, version)` courant porte déjà le marqueur `.cockpit-frontmap-built` (rien
    à faire). Sinon : matérialise l'arbre (`git archive <sha>`) puis `frontmap build --root`, et écrit le
    marqueur APRÈS un build réussi (rc 0). `sot` est le SoT bare du projet (résolu par l'appelant). Lève
    `FrontmapError`
    (réf absente, archive ou build en échec, version d'outil illisible)."""
    git = InternalGit()
    try:
        sha = git.feature_sha(sot, ref)         # rev-parse <ref> → SHA plein (part de la clé de cache)
    except GitOpError as exc:
        raise FrontmapError(f"réf introuvable ({ref}) pour {project} : {exc}") from exc

    version = frontmap_version(runner)          # version d'outil → l'autre moitié de la clé
    root = index_dir_for(settings, project, sha, version)
    marker = root / _BUILT_MARKER
    if marker.is_file():                        # cache hit : index déjà bâti pour ce (SHA, version)
        return IndexHandle(project=project, ref=ref, sha=sha, root=root)

    root.mkdir(parents=True, exist_ok=True)
    try:
        git.archive(sot, sha, root)             # matérialise l'arbre du SHA (déterministe, immuable)
    except GitOpError as exc:
        raise FrontmapError(f"matérialisation impossible ({project}@{ref}) : {exc}") from exc

    res = runner(frontmap_argv("build", "--root", str(root)), timeout=BUILD_TIMEOUT_S)
    if not res.ok:                              # rc du CLI = contrat de succès (pas de peek fichier interne)
        raise FrontmapError(
            f"`frontmap build` a échoué ({project}@{ref}) : {res.stderr.strip()[:200]}")
    marker.write_text(version, encoding="utf-8")  # marqueur cockpit APRÈS build OK → cache-hit ultérieur
    return IndexHandle(project=project, ref=ref, sha=sha, root=root)
