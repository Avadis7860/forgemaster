"""build_provenance — provenance de BUILD de forgemaster + signal honnête de fraîcheur (staleness).

Symétrique du tampon de provenance PAR-PROJET (`projects.registry` → `.forgemaster/provenance.toml`), mais
pour forgemaster **lui-même** : de quel commit le wheel installé a-t-il été bâti, et est-il en retard sur le
SoT qu'il sert ? Répond au cap silencieux qui a laissé une VM tourner 119 commits en retard (type de bundle
manquant) sans un mot.

Invariants du repo (`CLAUDE.md`), cités **tels qu'ils y sont définis** : **fraîcheur par SHA de HEAD, jamais
mtime** · **jamais de cap silencieux** (un substrat périmé DOIT se déclarer, jamais faux-vert) · **transport
local** (`core.run`, zéro ssh/proxmox/CT/`/home/dev`) · **I/O injectable** (résolveurs en argument → cœur
testable, calqué sur `auth.claude_auth_status`).

CONTRAINTE PROPRE À CE MODULE, nommée à part parce qu'elle n'est PAS dans `transport local` : **aucun accès
réseau**. Elle vient de l'usage — ce module sert `GET /version`, une sonde qui ne doit ni pendre ni rendre
500 parce qu'un amont est injoignable — et non de l'invariant, qui interdit d'orchestrer des hôtes distants
(ssh/proxmox/CT), pas de parler au réseau : `tools.py` fait `pip install git+https://…` sans rien violer.
Elle a été fondue dans la citation de l'invariant jusqu'au 2026-08-04, ce qui a produit un contresens gravé
dans un post-mortem (corrigé PR #1298) : **un invariant cité fait autorité**, donc il se cite exactement.

La sonde porte TROIS volets, étiquetés séparément : le **wheel** (ce module), les **cartes hôte** servies par
`tools/venv` (`maps`, lues par `tools.maps_provenance`) et le **serveur MCP de corpus** (`mcp`, lu par
`mcp.local.topology`). Les trois bougent indépendamment — le wheel à la réinjection, les cartes à `forgemaster
tools install`, le serveur à l'édition — donc un verdict unique serait faux dès que l'un bouge seul. Tous se
lisent LOCALEMENT ; les comparaisons à l'amont restent explicites (`forgemaster toolchain check` pour les
cartes,
`GET /version` du serveur pour un MCP distant), jamais dans un chemin chaud.

Trois états honnêtes, jamais un faux-vert :
- `sha=None` : wheel sans tampon (checkout éditable/dev) → provenance inconnue, on ne PRÉTEND rien ;
- `comparable=False` : pas de miroir SoT local à comparer (install publique) → provenance seule ;
- `comparable=True` : `stale`/`behind_by`/`missing_types` calculés **par SHA** contre le HEAD du miroir.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from forgemaster import __version__
from forgemaster.git.internal import InternalGit

if TYPE_CHECKING:
    from forgemaster.config import Settings

_STAMP = Path(__file__).with_name("_build.json")   # embarqué au wheel (build-wheel.sh), gitignoré en dev
_TYPES_TREE = "src/forgemaster/provision/bundles/types"


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
    """Miroir SoT bare **LOCAL** de forgemaster lui-même (si forgemaster est un projet géré). Chemin inliné
    pour ne
    pas dépendre de `projects.registry.sot_path_for` → évite le cycle registry↔build_provenance."""
    return settings.projects_root / "forgemaster" / "sot.git"


def _installed_types() -> tuple[str, ...]:
    """Types de bundle du forgemaster INSTALLÉ (lazy import : aucun cycle au chargement du module)."""
    from forgemaster.provision import discover_types
    return tuple(discover_types())


def _served_maps(settings: Settings) -> list[dict]:
    """Les 3 cartes hôte servies par `tools/venv` (lazy import, même convention que `_installed_types`).
    Lecture LOCALE de `direct_url.json` — zéro réseau, comme le reste de cette sonde. **Ne lève jamais** :
    une lecture impossible dégrade en liste vide plutôt que de faire tomber `/api/version`."""
    try:
        from forgemaster.tools import maps_provenance
        return maps_provenance(settings)
    except Exception:
        return []


def _mcp_topology(settings: Settings) -> dict:
    """La topologie MCP de cette instance (lazy import, même convention que `_served_maps` — `mcp.local`
    tire `provision.mcp` et `tools`, on ne les charge pas à l'import de ce module). Lecture LOCALE, zéro
    réseau. **Ne lève jamais** : une sonde illisible dégrade en `unknown` plutôt que de faire tomber
    `/api/version`."""
    try:
        from forgemaster.mcp.local import topology
        return topology(settings)
    except Exception:
        return {"topology": "unknown", "sha": None, "endpoint": None,
                "reason": "topologie MCP illisible sur cet hôte"}


def provenance(settings: Settings, *, installed_types: tuple[str, ...] | None = None,
               stamp: Path | None = None, git: InternalGit | None = None,
               mirror_git_dir: Path | None = None, maps: list[dict] | None = None,
               mcp: dict | None = None) -> dict:
    """Détecteur live : compose le tampon embarqué + la fraîcheur mesurée contre le miroir SoT **local**
    + les **cartes hôte servies** (`maps`). **Enveloppé, ne lève jamais** — un miroir absent/cassé dégrade
    honnêtement en `comparable=False` (jamais de 500 sur la sonde onboarding, jamais de faux-vert). I/O via
    le seul seam `InternalGit` (transport local, injectable comme `stamp`/`installed_types`/`mirror_git_dir`/
    `maps` pour les tests).

    `maps` répond à « quelles cartes cette instance sert-elle ? », que rien ne savait dire : le wheel et les
    3 cartes vieillissent SÉPARÉMENT (le wheel à la réinjection, les cartes à `tools install`), les fondre
    en un seul verdict mentirait dans les deux sens. Ce champ RAPPORTE un SHA déjà présent sur le disque ;
    savoir s'il est en retard exige l'amont et reste **explicite** (`forgemaster toolchain check`).

    `mcp` répond à la question jumelle pour le serveur de corpus — **laquelle des deux topologies déclarées
    (§4 de la décision d'édition du 2026-08-02) cette instance est-elle ?** Co-installé, le serveur est une
    pièce de l'édition et porte un SHA lisible ici ; distant, il est un service dont on dépend et dont le
    SHA ne se lit pas sans requête. Le champ dit lequel des deux, plutôt que de laisser deviner."""
    stampd = read_stamp(stamp)
    build_sha = stampd["sha"]
    base = {"version": __version__, "sha": build_sha, "committed_at": stampd["committed_at"],
            "maps": maps if maps is not None else _served_maps(settings),
            "mcp": mcp if mcp is not None else _mcp_topology(settings)}
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
    """Message actionnable **ssi** le forgemaster est périmé ET `project_type` fait partie des types apparus
    depuis le build (`missing_types`). Sinon `None` (l'appelant garde son message d'origine). Ne lève jamais
    (`provenance` est total). Formulé « miroir forgemaster **local** », jamais « upstream »."""
    p = prov if prov is not None else provenance(settings)
    if p.get("stale") and project_type in p.get("missing_types", []):
        sha = (p.get("sha") or "?")[:12]
        return (f"forgemaster est en retard de {p.get('behind_by')} commit(s) sur ton miroir forgemaster "
        f"local "
                f"(bâti depuis {sha}) — le type {project_type!r} a été ajouté depuis. Réinjecte un wheel "
                f"frais (deploy/build-wheel.sh → reinject_forgemaster_wheel.sh).")
    return None
