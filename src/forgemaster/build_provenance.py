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

La sonde porte TROIS volets d'IDENTITÉ, étiquetés séparément : le **wheel** (ce module), les **cartes hôte**
servies par `tools/venv` (`maps`, lues par `tools.maps_provenance`) et le **serveur MCP de corpus** (`mcp`, lu
par `mcp.local.topology`). Les trois bougent indépendamment — le wheel à la réinjection, les cartes à
`forgemaster toolchain install`, le serveur à l'édition — donc un verdict unique serait faux dès que l'un
bouge seul. Tous se lisent LOCALEMENT ; ce qui exige le réseau reste explicite et hors d'ici (`GET /version`
du serveur pour un MCP distant), jamais dans un chemin chaud.

Deux volets de plus **répondent** à ce que les trois premiers ne faisaient que décrire — *quelle ÉDITION
tourne ici ?* : `install` dit de quel MODE d'install l'instance vient (déduit du disque, jamais déclaré) et
`edition` confronte les cartes SERVIES à celles que l'édition installée DÉCLARE (`tools.check_tools`, local
et sans réseau depuis le 2026-08-08). Le premier ferme le trou « deux modes d'install coexistent et rien ne
remonte lequel est actif » ; le second met à portée d'une surface — donc de qui n'a pas de terminal — un
verdict qui ne vivait que dans la CLI.

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
      on n'invente pas ce qu'on n'a pas pu lire).

    `head` est **rendu tel qu'il a été lu, même quand `comparable=False`** : « je connais la référence mais
    pas mon propre build » est un état plus honnête que deux `null` — et sans lui, une surface ne peut pas
    DIRE contre quoi elle compare (ni s'en servir comme clé de rappel). Le verdict, lui, ne change pas d'un
    iota : c'est toujours `build_sha` ET `head` qui le conditionnent."""
    if not build_sha or not head:
        return {"comparable": False, "stale": None, "behind_by": None, "missing_types": [], "head": head}
    stale = build_sha != head
    missing = (sorted(set(remote_types) - set(installed_types))
               if (stale and remote_types is not None) else [])
    return {"comparable": True, "stale": stale, "head": head,
            "behind_by": 0 if not stale else behind_by, "missing_types": missing}


def install_mode(build_sha: str | None, edition_readable: bool) -> dict:
    """Fonction **PURE** (zéro I/O) : de quel MODE d'install cette instance vient — `{mode, reason}`.

    Deux faits lisibles localement, et leur conjonction EST le mode : le wheel porte-t-il son tampon de
    build (`_build.json`) ? l'édition des cartes (`forgemaster/_maps/maps.json`) est-elle lisible ? Le
    patron vient de `mcp.local.topology`, et sa raison est la même : **déduit du disque, jamais déclaré**.
    Une clé d'env `…_MODE` serait un champ qui peut mentir — rien ne le re-vérifie après une réinstall.

    Quatre issues, dont aucune n'est une panne en soi :

    - **`edition`** — wheel de release portant les cartes qu'il épingle. Le mode canonique depuis le
      2026-08-08.
    - **`wheel`** — wheel bâti, mais sans édition embarquée : soit d'avant que les cartes y entrent, soit un
      build dégradé (`hatch_build` le signale déjà par un warning). Distinguer les deux évite d'appeler
      « checkout » un artefact qui n'en est pas un.
    - **`checkout`** — sibling éditable : ni tampon, ni édition. C'est le mode de **développement**, normal
      et attendu, pas un défaut. Nuance mesurée : `deploy/build-wheel.sh` écrit `src/forgemaster/_build.json`
      (gitignoré) dans l'arbre source, donc un checkout qui a déjà bâti un wheel rend `wheel` — c'est ce que
      son disque dit, et c'était déjà la sémantique de `sha` avant ce champ.
    - **`unknown`** — édition lisible SANS tampon : les deux artefacts de build ne peuvent pas venir de
      builds différents, donc on ne conclut rien et on DIT l'incohérence.

    Pourquoi ce champ existe alors que `sha is None` « suffisait » : il ne suffisait que tant qu'un seul
    candidat le satisfaisait. Un wheel bâti sans tampon aurait été annoncé « checkout » — c'est la leçon de
    la phase 3a·5a, *une dérivation exacte dans un cas devient un choix arbitraire dans l'autre*."""
    if build_sha:
        if edition_readable:
            return {"mode": "edition", "reason": None}
        # La raison énonce l'OBSERVABLE (« aucune édition lisible »), pas une cause (« bâti sans »). Les
        # deux se confondent presque toujours, mais pas quand la lecture a échoué : affirmer l'absence
        # là où l'on n'a pas su lire serait le « je n'ai pas pu mesurer » rendu comme un « non » (3a·5b).
        return {"mode": "wheel", "reason": (
            "aucune édition de cartes lisible sous `forgemaster/_maps` — cette instance ne peut pas "
            "reposer ses 3 cartes hors-ligne (un wheel de release les embarque : `deploy/build-wheel.sh`)")}
    if edition_readable:
        return {"mode": "unknown", "reason": (
            "l'édition des cartes est lisible mais le wheel n'est pas tamponné — les deux artefacts sont "
            "posés par le même build, cette paire est donc incohérente et on n'en conclut rien")}
    return {"mode": "checkout", "reason": (
        "ni tampon de build ni édition embarquée — cette instance vient d'un checkout éditable, pas d'un "
        "wheel (mode de développement : normal, pas une panne)")}


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


def _edition(settings: Settings, served: list[dict] | None) -> dict:
    """Ce que l'édition installée DÉCLARE, confronté à ce qui est SERVI (lazy import, même convention que
    `_served_maps`). Délègue à `tools.check_tools` **verbatim** : la CLI (`forgemaster toolchain check`) et
    cette route rendent alors le MÊME objet, donc elles ne peuvent pas diverger. Lecture locale, zéro
    réseau, zéro subprocess. **Ne lève jamais** — une lecture impossible dégrade en `unknown` porteur de sa
    raison, jamais en vert.

    `served` est passé plutôt que relu : le volet `maps` vient de sortir du même `.dist-info`. Quand ce
    volet a dégradé en liste **vide**, l'état est `unknown` (garde de `overall_state`) mais il n'aurait
    **aucune raison** : l'édition, elle, a pu être lue — donc `check_tools` n'a rien à noter. On la pose
    ici, où le fait est connu, sinon une surface annoncerait « au moins une carte n'a pas pu être
    comparée » en n'en montrant aucune."""
    try:
        from forgemaster.tools import check_tools
        rapport = check_tools(settings, served=served)
    except Exception:
        return {"edition_dir": None, "state": "unknown", "maps": [],
                "reason": "édition installée illisible sur cet hôte"}
    if not rapport["maps"] and not rapport["reason"]:
        rapport["reason"] = ("l'outillage de cet hôte n'est pas lisible — aucune carte servie n'a pu être "
                             "lue, donc rien n'a pu être confronté à l'édition")
    return rapport


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
               mcp: dict | None = None, edition: dict | None = None) -> dict:
    """Détecteur live : compose le tampon embarqué + la fraîcheur mesurée contre le miroir SoT **local**
    + les **cartes hôte servies** (`maps`). **Enveloppé, ne lève jamais** — un miroir absent/cassé dégrade
    honnêtement en `comparable=False` (jamais de 500 sur la sonde onboarding, jamais de faux-vert). I/O via
    le seul seam `InternalGit` (transport local, injectable comme `stamp`/`installed_types`/`mirror_git_dir`/
    `maps` pour les tests).

    `maps` répond à « quelles cartes cette instance sert-elle ? », que rien ne savait dire : le wheel et les
    3 cartes vieillissent SÉPARÉMENT (le wheel à la réinjection, les cartes à `tools install`), les fondre
    en un seul verdict mentirait dans les deux sens. Ce champ RAPPORTE un SHA déjà présent sur le disque ;
    juger cette carte est l'affaire du volet `edition` ci-dessous.

    `mcp` répond à la question jumelle pour le serveur de corpus — **laquelle des deux topologies déclarées
    (§4 de la décision d'édition du 2026-08-02) cette instance est-elle ?** Co-installé, le serveur est une
    pièce de l'édition et porte un SHA lisible ici ; distant, il est un service dont on dépend et dont le
    SHA ne se lit pas sans requête. Le champ dit lequel des deux, plutôt que de laisser deviner.

    `install` et `edition` referment la question que ces trois volets posaient sans y répondre — *quelle
    ÉDITION tourne ici ?* Les volets ci-dessus disent ce que l'instance **sert** ; `edition` dit ce que
    l'édition installée **DÉCLARE**, et c'est leur **écart** qui rend lisible une instance montée sans
    reposer son outillage. Il vient de `tools.check_tools` **verbatim** — donc de la même lecture que
    `forgemaster toolchain check`, jamais d'une seconde qui divergerait. Le verdict de conformité vivait
    jusqu'au 2026-08-08 dans la seule CLI : hors de portée de qui n'a pas de terminal, c'est-à-dire de la
    personne pour qui tout ce cycle existe."""
    stampd = read_stamp(stamp)
    build_sha = stampd["sha"]
    # La RÉFÉRENCE est résolue avant tout le reste et voyage dans `base` : elle doit être dite sur les TROIS
    # sorties, y compris les deux dégradées. Une surface qui annonce un verdict sans nommer ce qu'elle a
    # comparé laisse l'utilisateur devant un « à jour » dont il ne peut pas juger la portée — et ce miroir-là
    # est LOCAL, donc lui-même vieillissant. `None` veut dire « aucune référence sur ce disque », jamais
    # « je n'ai pas regardé ».
    try:
        mgd: Path | None = (Path(mirror_git_dir) if mirror_git_dir is not None
                            else _mirror_git_dir(settings))
        # Le `is not None` n'est PAS redondant : l'annotation `Path | None` (nécessaire — la variable est
        # réassignée à `None` juste après) élargit le type dès la déclaration, et mypy refuse `.exists()`
        # sans lui. Mesuré, pas supposé : le retirer sort `union-attr` au gate.
        if mgd is not None and not mgd.exists():
            mgd = None
    except Exception:
        mgd = None
    # Les cartes servies sont lues UNE fois et passées à `_edition` : le verdict de conformité les compare,
    # il n'a aucune raison de re-parcourir les mêmes `.dist-info`. Deux marches, une liste (acquis 2a‴).
    servies = maps if maps is not None else _served_maps(settings)
    ed = edition if edition is not None else _edition(settings, servies)
    base = {"version": __version__, "sha": build_sha, "committed_at": stampd["committed_at"],
            "reference": str(mgd) if mgd is not None else None,
            # `edition_dir` n'est renseigné que quand le manifeste a été LU — c'est donc exactement le fait
            # dont le mode a besoin, et il n'y a pas de seconde lecture pour l'obtenir.
            "install": install_mode(build_sha, ed.get("edition_dir") is not None),
            "maps": servies, "edition": ed,
            "mcp": mcp if mcp is not None else _mcp_topology(settings)}
    try:
        if mgd is None:
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
