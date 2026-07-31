"""build_provenance — provenance de BUILD de cockpit + signal honnête de fraîcheur (staleness).

Symétrique du tampon de provenance PAR-PROJET (`projects.registry` → `.cockpit/provenance.toml`), mais pour
cockpit **lui-même** : de quel commit le wheel installé a-t-il été bâti, et est-il en retard sur le SoT
qu'il sert ? Répond au cap silencieux qui a laissé une VM tourner 119 commits en retard (type de bundle
manquant) sans un mot. Invariants (`CLAUDE.md`) : **fraîcheur par SHA de HEAD, jamais mtime** · **jamais de
cap silencieux** (un substrat périmé DOIT se déclarer, jamais faux-vert) · **transport local** (miroir bare
LOCAL via le seul seam `InternalGit`, zéro réseau) · **I/O injectable** (résolveurs en argument → cœur
testable, calqué sur `auth.claude_auth_status`).

Trois états honnêtes, jamais un faux-vert :
- `sha=None` : wheel sans tampon (checkout éditable/dev) → provenance inconnue, on ne PRÉTEND rien ;
- `comparable=False` : pas de miroir SoT local à comparer (install publique) → provenance seule ;
- `comparable=True` : `stale`/`behind_by`/`missing_types` calculés **par SHA** contre le HEAD du miroir.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from cockpit import __version__
from cockpit.git.internal import InternalGit

if TYPE_CHECKING:
    from cockpit.config import Settings

_STAMP = Path(__file__).with_name("_build.json")   # embarqué au wheel (build-wheel.sh), gitignoré en dev
_TYPES_TREE = "src/cockpit/provision/bundles/types"


def read_stamp(stamp_path: Path | None = None) -> dict:
    """Le tampon de build embarqué `{sha, committed_at}` — ou `{sha:None,…}` honnête s'il est absent
    (checkout éditable : le wheel n'a pas été bâti par `build-wheel.sh`). **Ne lève jamais** : provenance
    inconnue est un état valide, pas une erreur."""
    p = stamp_path or _STAMP
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sha": None, "committed_at": None}
    return {"sha": data.get("sha") or None, "committed_at": data.get("committed_at") or None}


def staleness(build_sha: str | None, head: str | None, *, behind_by: int | None = None,
              installed_types: tuple[str, ...] = (),
              remote_types: tuple[str, ...] | None = None) -> dict:
    """Fonction **PURE** (zéro I/O) : compare le SHA de build au HEAD du miroir. Les faits (head, behind_by,
    remote_types) sont fournis par l'appelant (résolveurs injectables). Verdict honnête, jamais faux-vert :
    - build inconnu OU pas de HEAD → `comparable=False` (on ne prétend rien) ;
    - sinon `stale = build_sha != head`, avec `behind_by`/`missing_types` si disponibles (sinon `None`/`[]`,
      on n'invente pas ce qu'on n'a pas pu lire)."""
    if not build_sha or not head:
        return {"comparable": False, "stale": None, "behind_by": None, "missing_types": []}
    stale = build_sha != head
    missing = (sorted(set(remote_types) - set(installed_types))
               if (stale and remote_types is not None) else [])
    return {"comparable": True, "stale": stale,
            "behind_by": 0 if not stale else behind_by, "missing_types": missing}


def _mirror_git_dir(settings: Settings) -> Path:
    """Miroir SoT bare **LOCAL** de cockpit lui-même (si cockpit est un projet géré). Chemin inliné pour ne
    pas dépendre de `projects.registry.sot_path_for` → évite le cycle registry↔build_provenance."""
    return settings.projects_root / "cockpit" / "sot.git"


def _installed_types() -> tuple[str, ...]:
    """Types de bundle du cockpit INSTALLÉ (lazy import : aucun cycle au chargement du module)."""
    from cockpit.provision import discover_types
    return tuple(discover_types())


def provenance(settings: Settings, *, installed_types: tuple[str, ...] | None = None,
               stamp: Path | None = None, git: InternalGit | None = None,
               mirror_git_dir: Path | None = None) -> dict:
    """Détecteur live : compose le tampon embarqué + la fraîcheur mesurée contre le miroir SoT **local**.
    **Enveloppé, ne lève jamais** — un miroir absent/cassé dégrade honnêtement en `comparable=False` (jamais
    de 500 sur la sonde onboarding, jamais de faux-vert). I/O via le seul seam `InternalGit` (transport
    local, injectable comme `stamp`/`installed_types`/`mirror_git_dir` pour les tests)."""
    stampd = read_stamp(stamp)
    build_sha = stampd["sha"]
    base = {"version": __version__, "sha": build_sha, "committed_at": stampd["committed_at"]}
    try:
        mgd = Path(mirror_git_dir) if mirror_git_dir is not None else _mirror_git_dir(settings)
        if not mgd.exists():
            return {**base, **staleness(build_sha, None)}       # pas de miroir → non comparable, honnête
        g = git or InternalGit()
        head = g.feature_sha(mgd, "HEAD")
        behind: int | None = None
        remote_types: tuple[str, ...] | None = None
        if build_sha:
            # Le HEAD est lisible ; `behind_by`/`missing_types` sont un PLUS (un build hors-miroir — commit
            # local non poussé — fait échouer ces lectures sans invalider le verdict stale déduit du HEAD).
            try:
                # `ahead` = commits du HEAD absents du build = de combien le build est EN RETARD.
                behind = g.ahead_behind(mgd, base=build_sha, head="HEAD").get("ahead")
                remote_types = tuple(e["name"] for e in g.ls_tree(mgd, "HEAD", _TYPES_TREE)
                                     if e.get("type") == "tree")
            except Exception:
                behind, remote_types = None, None
        inst = installed_types if installed_types is not None else _installed_types()
        return {**base, **staleness(build_sha, head, behind_by=behind,
                                    installed_types=inst, remote_types=remote_types)}
    except Exception:
        return {**base, **staleness(build_sha, None)}


def stale_type_hint(settings: Settings, project_type: str, *, prov: dict | None = None) -> str | None:
    """Message actionnable **ssi** le cockpit est périmé ET `project_type` fait partie des types apparus
    depuis le build (`missing_types`). Sinon `None` (l'appelant garde son message d'origine). Ne lève jamais
    (`provenance` est total). Formulé « miroir cockpit **local** », jamais « upstream »."""
    p = prov if prov is not None else provenance(settings)
    if p.get("stale") and project_type in p.get("missing_types", []):
        sha = (p.get("sha") or "?")[:12]
        return (f"cockpit est en retard de {p.get('behind_by')} commit(s) sur ton miroir cockpit local "
                f"(bâti depuis {sha}) — le type {project_type!r} a été ajouté depuis. Réinjecte un wheel "
                f"frais (deploy/build-wheel.sh → reinject_cockpit_wheel.sh).")
    return None
