"""index — matérialisation + build de l'index code-map d'un projet, CACHÉ par (SHA, schema_version).

Un projet est un SoT git **bare** + worktrees **éphémères** : aucun checkout source stable à cartographier.
On matérialise donc l'arbre d'une réf (défaut `dev`) via `InternalGit.archive` dans un cache dérivé sous
`settings.home/"codemap"/<projet>/<sha>/<schema_version>/`, puis on y lance `codemap build` (subprocess,
seam `core.run`).

Fraîcheur **(SHA, schema)-bound** : un couple (SHA de la réf, version de contrat de code-map) = un dossier.
L'index est reconstruit si le SHA change (nouveau code) **OU** si `schema_version` change (upgrade de
code-map : nouveaux champs/verbes) — ce dernier ferme le trou « il fallait vider le cache à la main après
un déploiement de code-map ». La version est lue via `codemap --schema-version` (négociation, **sans index**).

**Découplage du layout interne de code-map** (P6) : le cache-hit n'inspecte plus un nom de fichier interne
(`.codemap/calls.manifest.json`) mais un **marqueur propre au cockpit** (`.cockpit-index-built`) écrit APRÈS
un build réussi. code-map reste consommé en **boîte-noire CLI** : on dépend de son **contrat** (rc,
`--schema-version`, stdout JSON), jamais de son arborescence de fichiers. Le `runner` (seam `core.run`) est
injectable pour le test.
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
SCHEMA_TIMEOUT_S = 30           # `codemap --schema-version` : imprime une constante et sort ; borné.
_BUILT_MARKER = ".cockpit-index-built"  # marqueur PROPRE au cockpit (découplé du layout interne code-map).


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


def codemap_schema_version(runner: Runner = run) -> str:
    """Version du CONTRAT de code-map (`codemap --schema-version`). **Sans index** : sert à câbler la clé de
    cache et négocier la compat AVANT tout build. Non mémoïsé (un `id(runner)` peut être réutilisé après GC →
    valeur périmée) : le CLI imprime une constante et sort, coût négligeable hors boucle chaude. Lève
    `CodemapError` si le CLI ne rend pas une version lisible."""
    res = runner(codemap_argv("--schema-version"), timeout=SCHEMA_TIMEOUT_S)
    version = res.stdout.strip()
    if not res.ok or not version:
        raise CodemapError(f"`codemap --schema-version` illisible : {res.stderr.strip()[:200]}")
    return version


def index_dir_for(settings: Settings, project: str, sha: str, schema: str) -> Path:
    """Dossier de cache dérivé pour l'index d'un `(projet, sha, schema)` :
    `home/codemap/<projet>/<sha>/<schema>`. Invalidé par SHA (nouveau code) **ET** par `schema` (upgrade de
    code-map) — un bump de contrat ouvre un dossier neuf, l'ancien index n'est jamais servi périmé."""
    return settings.home / "codemap" / project / sha / schema


def ensure_index(
    settings: Settings, project: str, sot: Path, *, ref: str = "dev", runner: Runner = run,
) -> IndexHandle:
    """Garantit un index code-map frais pour `(project, ref)` et renvoie son `IndexHandle`.

    Cache hit si le dossier `(sha, schema_version)` courant porte déjà le marqueur `.cockpit-index-built`
    (rien à faire). Sinon : matérialise l'arbre (`git archive <sha>`) puis `codemap build --root`, et écrit
    le marqueur APRÈS un build réussi (rc 0). `sot` est le SoT bare du projet (résolu par l'appelant). Lève
    `CodemapError` (réf absente, archive ou build en échec, version de contrat illisible)."""
    git = InternalGit()
    try:
        sha = git.feature_sha(sot, ref)         # rev-parse <ref> → SHA plein (part de la clé de cache)
    except GitOpError as exc:
        raise CodemapError(f"réf introuvable ({ref}) pour {project} : {exc}") from exc

    schema = codemap_schema_version(runner)     # version de contrat → l'autre moitié de la clé
    root = index_dir_for(settings, project, sha, schema)
    marker = root / _BUILT_MARKER
    if marker.is_file():                        # cache hit : index déjà bâti pour ce (SHA, schema)
        return IndexHandle(project=project, ref=ref, sha=sha, root=root)

    root.mkdir(parents=True, exist_ok=True)
    try:
        git.archive(sot, sha, root)             # matérialise l'arbre du SHA (déterministe, immuable)
    except GitOpError as exc:
        raise CodemapError(f"matérialisation impossible ({project}@{ref}) : {exc}") from exc

    res = runner(codemap_argv("build", "--root", str(root)), cwd=root, timeout=BUILD_TIMEOUT_S)
    if not res.ok:                              # rc du CLI = contrat de succès (plus de peek fichier interne)
        raise CodemapError(
            f"`codemap build` a échoué ({project}@{ref}) : {res.stderr.strip()[:200]}")
    marker.write_text(schema, encoding="utf-8")  # marqueur cockpit APRÈS build OK → cache-hit ultérieur
    return IndexHandle(project=project, ref=ref, sha=sha, root=root)
