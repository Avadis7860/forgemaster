"""provision.derive — projection **build-time** d'un template corpus vers le seed vendoré d'un bundle.

Le seed d'un bundle (`bundles/types/<type>/`) n'est plus tapé à la main : c'est la **projection dérivée** d'un
template SoT (`corpus/templates/<blueprint>/scaffold.md`, vendoré ici sous `derive/<type>/`). La distillation
enrichit le template → on relance `apply_derivation` → le seed régénéré fait naître le projet suivant + riche
(SoT-and-derive, north-star v3). C'est le **levier de compounding** — pas « worker → blueprint central »
(cf. décision vault `cockpit-dispatch-blueprint-severance`).

**Deux temps, un verrou préservé.** Toute lecture corpus / remplissage de jeton / anti-drift vit ICI, au
**build** (`cockpit bundle derive`), et le résultat est **commité** dans l'overlay. Le seed-time
(`create_project → load_bundle → init_sot`) reste **verbatim / offline / déterministe** : il lit les
octets déjà dérivés, ne re-dérive JAMAIS, n'appelle ni MCP ni ce module. Ne câble donc jamais `derive_type`/
`apply_derivation` sur le chemin de création.

**Deux classes de jetons.** Les jetons *archétype* (`{{gate_cmd}}`, `{{ts_version}}`… constants pour le type)
sont remplis au build. Les jetons *projet* (`{{game_name}}`, `{{theme}}`… propres à la mission) restent
`{{…}}` dans l'overlay commité, voyagent dans le seed et sont remplis par le worker — c'est le contrat
« runnable, remplace les jetons » du template précédent `seed-runnable-config`.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).parent
_DERIVE_DIR = _HERE / "derive"                     # sources vendorées (non-seedées) : scaffold.md, values…
_BUNDLES_DIR = _HERE / "bundles"                   # cible : l'overlay du type
_MANIFEST_NAME = "derived.manifest.json"           # tampon build : template/sha des sources, chemins managés
DERIVE_VERSION = "1"

_SECTION = re.compile(
    r"^###\s+`(?P<path>[^`]+)`[^\n]*\n+```[a-z0-9]*\n(?P<body>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)
_JETON = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
_STEP = re.compile(r"^-\s+\*\*É(\d+)\s+—\s+([^*]+?)\s*\*\*", re.MULTILINE)


class DeriveError(ValueError):
    """Une dérivation build-time échoue : sources vendorées absentes, jeton archétype inconnu (ni rempli ni
    dans l'allowlist projet), ou sentinelles de splice introuvables. Sous-classe de `ValueError` → routée en
    400 (API) / message CLI, sans handler dédié."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derivable_types() -> tuple[str, ...]:
    """Les types dont le seed est **dérivé** (un sous-dossier `derive/<type>/`). Trié, déterministe."""
    if not _DERIVE_DIR.is_dir():
        return ()
    return tuple(sorted(d.name for d in _DERIVE_DIR.iterdir() if d.is_dir()))


def _derive_dir(project_type: str) -> Path:
    d = _DERIVE_DIR / project_type
    if not d.is_dir():
        raise DeriveError(
            f"type {project_type!r} non dérivé (aucun derive/{project_type}/) — dérivables : "
            f"{' | '.join(derivable_types()) or '(aucun)'}")
    return d


def _overlay_dir(project_type: str) -> Path:
    return _BUNDLES_DIR / "types" / project_type


def _config(project_type: str) -> dict:
    """La config d'instanciation `derive/<type>/values.toml` : `[template].ref`, `[archetype]` (fill),
    `[project].allow` (jetons autorisés à rester `{{…}}`)."""
    raw = (_derive_dir(project_type) / "values.toml").read_text(encoding="utf-8")
    return tomllib.loads(raw)


def parse_scaffold(md: str) -> list[tuple[str, str]]:
    """Découpe un template scaffold (FORME `seed-runnable-config`) en `[(chemin, corps)]` : chaque section
    `### \\`<chemin>\\`` suivie de son bloc fencé devient un fichier managé. Corps sans le newline final du
    fence (ajouté à l'écriture)."""
    return [(m.group("path"), m.group("body")) for m in _SECTION.finditer(md)]


def fill_jetons(body: str, values: dict[str, str], *, allow: set[str]) -> str:
    """Remplit les jetons `{{k}}` présents dans `values` ; laisse `{{k}}` intact si `k ∈ allow` (jeton
    projet) ; lève `DeriveError` pour tout autre jeton (archétype mal typé attrapé AVANT de partir en
    placeholder muet dans le seed)."""
    def _repl(m: re.Match[str]) -> str:
        k = m.group(1)
        if k in values:
            return values[k]
        if k in allow:
            return m.group(0)
        raise DeriveError(f"jeton inconnu {{{{{k}}}}} : ni valeur archétype ni jeton projet allowlisté")
    return _JETON.sub(_repl, body)


def blueprint_step_chain(blueprint_md: str) -> str:
    """La chaîne du patron d'étapes du blueprint (É1→…→Én, l'amorçage É0 exclu), dérivée des **titres**
    d'étape — source unique de vérité du pattern, au lieu d'une paraphrase hand-typed divergente."""
    steps = sorted((int(n), title.strip()) for n, title in _STEP.findall(blueprint_md) if int(n) >= 1)
    return " → ".join(f"É{n} {title}" for n, title in steps)


def splice_region(host: str, marker: str, block: str) -> str:
    """Remplace le contenu entre `<!-- derived:<marker>:start -->` et `:end` par `block` (dérivation
    **chirurgicale** d'une région : le reste du fichier hôte, hand-authored, est préservé). Lève
    `DeriveError` si les sentinelles sont absentes ou mal ordonnées."""
    start, end = f"<!-- derived:{marker}:start -->", f"<!-- derived:{marker}:end -->"
    i, j = host.find(start), host.find(end)
    if i == -1 or j == -1 or j < i:
        raise DeriveError(f"sentinelles derived:{marker} absentes ou mal ordonnées dans l'hôte")
    return host[:i] + start + "\n" + block + "\n" + host[j:]


@dataclass(frozen=True)
class DeriveResult:
    """Le résultat pur (aucune écriture) d'une dérivation : `files` = mapping `chemin-relatif-overlay →
    contenu managé désiré ; les `*_sha` tracent les sources vendorées (fraîcheur, futur `--refresh`)."""
    template_ref: str
    files: dict[str, str]
    template_sha: str
    blueprint_sha: str
    values_sha: str


def derive_type(project_type: str) -> DeriveResult:
    """Dérive (en mémoire, offline) le set de fichiers managés de l'overlay d'un type depuis ses sources
    vendorées. Fichiers **entiers** issus du scaffold (jetons archétype remplis, jetons projet laissés) +
    la **région §6** du `CLAUDE.md` semé, splicée depuis le patron d'étapes du blueprint (le reste du
    `CLAUDE.md` reste hand-authored : re-lit l'overlay courant comme base → idempotent)."""
    d = _derive_dir(project_type)
    scaffold = (d / "scaffold.md").read_text(encoding="utf-8")
    blueprint = (d / "blueprint.md").read_text(encoding="utf-8")
    values_raw = (d / "values.toml").read_text(encoding="utf-8")
    cfg = tomllib.loads(values_raw)
    archetype = {str(k): str(v) for k, v in cfg.get("archetype", {}).items()}
    allow = {str(x) for x in cfg.get("project", {}).get("allow", [])}
    template_ref = str(cfg["template"]["ref"])

    files: dict[str, str] = {}
    for rel, body in parse_scaffold(scaffold):
        files[rel] = fill_jetons(body, archetype, allow=allow).rstrip("\n") + "\n"

    overlay_claude = (_overlay_dir(project_type) / "CLAUDE.md").read_text(encoding="utf-8")
    chain = blueprint_step_chain(blueprint)
    bullet = (
        f"- **Blueprint d'abord** : applique le patron d'étapes ({chain}) et les décisions verrouillées "
        f"(serveur-autoritatif, déterminisme). *(Dérivé du blueprint `browser-game-pve` — ne pas éditer "
        f"à la main ; `cockpit bundle derive`.)*")
    files["CLAUDE.md"] = splice_region(overlay_claude, "blueprint-pattern", bullet)

    return DeriveResult(template_ref=template_ref, files=files, template_sha=_sha256(scaffold),
                        blueprint_sha=_sha256(blueprint), values_sha=_sha256(values_raw))


def apply_derivation(project_type: str) -> list[str]:
    """**Build-time uniquement** : écrit le set dérivé dans l'overlay et rafraîchit `derived.manifest.json`.
    JAMAIS appelé au seed. Retourne les chemins managés (triés)."""
    res = derive_type(project_type)
    overlay = _overlay_dir(project_type)
    for rel, content in res.files.items():
        dest = overlay / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    manifest = {
        "template": res.template_ref,
        "template_sha": res.template_sha,
        "blueprint_sha": res.blueprint_sha,
        "values_sha": res.values_sha,
        "generator_version": DERIVE_VERSION,
        "managed_paths": sorted(res.files),
    }
    (_derive_dir(project_type) / _MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sorted(res.files)


def check_drift(project_type: str) -> list[str]:
    """Chemins managés dont l'overlay vendoré diverge du dérivé (`[]` = en phase). La garde qui interdit au
    seed de re-diverger du template après la décision de dérivation — câblée en pytest ET en
    `cockpit bundle derive --check`."""
    res = derive_type(project_type)
    overlay = _overlay_dir(project_type)
    drift: list[str] = []
    for rel, content in res.files.items():
        cur = overlay / rel
        if not cur.is_file() or cur.read_text(encoding="utf-8") != content:
            drift.append(rel)
    return sorted(drift)


def template_provenance(project_type: str) -> tuple[str, str] | None:
    """`(template_ref, template_sha)` du seed d'un type, lu depuis `derived.manifest.json` (**lecture fichier
    offline**, jamais une dérivation) — pour tamponner la provenance par-projet au seed. `None` si le type
    n'est pas dérivé (aucun manifeste) : le tampon reste alors 3-clés (`bundle@version`)."""
    if project_type not in derivable_types():
        return None
    path = _DERIVE_DIR / project_type / _MANIFEST_NAME
    if not path.is_file():
        return None
    m = json.loads(path.read_text(encoding="utf-8"))
    return str(m["template"]), str(m["template_sha"])
