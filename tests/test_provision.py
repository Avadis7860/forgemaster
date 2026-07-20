"""Tests de provision — les bundles vendorés (base ⊕ overlay) chargés en mapping chemin→contenu."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from cockpit.provision import (
    discover_types,
    facet,
    list_valid_types,
    load_bundle,
    load_launch_roadmap,
    load_payload,
    read_bundle_manifest,
    validate_bundle,
)

# Fichiers-clés attendus dans le payload (dont dotfiles / dossiers cachés).
_EXPECTED = (
    "CLAUDE.md",
    ".gitignore",
    ".docsmap.toml",
    ".codemap.toml",
    ".frontmap.toml",
    "docs/architecture.md",
    ".claude/settings.json",
    ".claude/skills/work-loop/SKILL.md",
    ".claude/skills/quality-gate/SKILL.md",
    ".claude/skills/roadmap-decompose/SKILL.md",
    ".claude/skills/docs-authoring/SKILL.md",
    # Ancrage doc par-dossier (base → tout type) : un README racine + un par dossier structurant.
    "README.md",
    "docs/README.md",
    ".claude/README.md",
)

# READMEs universels (dans la base → présents dans TOUT type) : racine + dossiers structurants de base.
_BASE_READMES = ("README.md", "docs/README.md", ".claude/README.md")
# READMEs par-dossier propres à un overlay : `src/` n'existe que chez les types qui portent une arbo source.
_SRC_README_TYPES = ("browser-game", "front-ts")

# Skills de méthodo (Phase 6) : ce qui rend un projet semé auto-travaillable — planifier + mémoriser,
# au-delà de la seule boucle git (work-loop/quality-gate). Présents dans la base → dans TOUT type.
_METHOD_SKILLS = (
    ".claude/skills/roadmap-decompose/SKILL.md",
    ".claude/skills/docs-authoring/SKILL.md",
)


def test_load_payload_has_toolkit_and_dotfiles():
    payload = load_payload()
    # Garde anti-régression : `.docsmap.toml` et `.claude/` sont des dotfiles — `iterdir()` DOIT les inclure
    # (un glob `*` shell-like les raterait). Chaque fichier-clé présent et non vide.
    for key in _EXPECTED:
        assert key in payload, f"payload manque {key}"
        assert payload[key].strip(), f"payload {key} vide"


def test_payload_is_generic_and_points_docsmap():
    payload = load_payload()
    # Générique : le CLAUDE.md semé oriente vers `docsmap where` (le levier) et n'a aucun slug en dur.
    assert "docsmap where" in payload["CLAUDE.md"]
    assert "work-loop" in payload[".claude/skills/work-loop/SKILL.md"]


def test_load_payload_is_deterministic():
    # Deux lectures → mapping identique (ordre trié, lecture pure de fichiers vendorés).
    assert load_payload() == load_payload()


# -- v6 (typed-bundles) : base ⊕ overlay ------------------------------------------------------------

_OVERLAY_TYPES = tuple(t for t in discover_types() if t != "generic")


def test_generic_equals_base_equals_load_payload():
    # `generic` = base seule ; `load_payload` est le shim de compat. Les trois coïncident exactement.
    assert load_bundle("generic") == load_bundle() == load_payload()


def test_load_bundle_rejects_unknown_type():
    with pytest.raises(ValueError, match="type de projet inconnu"):
        load_bundle("rust-cli")


def test_discover_types_from_filesystem():
    # Le registre = le filesystem : `generic` en tête + un dossier par overlay sous bundles/types/.
    types = discover_types()
    assert types[0] == "generic"
    assert set(_OVERLAY_TYPES) <= set(types)


def test_read_bundle_manifest_has_version_and_facets():
    for t in discover_types():
        m = read_bundle_manifest(t)
        assert m.get("version"), f"{t} : version manquante au manifeste"
        assert m["project_type"] == t
        assert m["default_facet"] in m["facets"]


def test_validate_bundle_passes_for_all_discovered():
    for t in discover_types():
        validate_bundle(t)          # ne lève pas : tous les bundles vendorés sont valides


def test_validate_bundle_rejects_broken(tmp_path, monkeypatch):
    # Un type découvert mais incohérent (default_facet hors facets) est refusé (fail-closed), sans toucher
    # au vrai registre : on monkeypatche `_BUNDLES_DIR` sur une arbo temporaire isolée.
    import cockpit.provision as prov
    meta = tmp_path / "bundles" / "base" / ".cockpit"
    meta.mkdir(parents=True)
    (meta / "bundle.toml").write_text(
        '[bundle]\nversion = "1"\nproject_type = "generic"\nfacets = ["doc"]\ndefault_facet = "doc"\n',
        encoding="utf-8")
    doc = tmp_path / "bundles" / "base" / ".claude" / "facets" / "doc"
    doc.mkdir(parents=True)
    (doc / "PERSONA.md").write_text("x", encoding="utf-8")
    broken = tmp_path / "bundles" / "types" / "broken" / ".cockpit"
    broken.mkdir(parents=True)
    (broken / "bundle.toml").write_text(
        '[bundle]\nversion = "1"\nproject_type = "broken"\nfacets = ["doc"]\ndefault_facet = "nope"\n',
        encoding="utf-8")
    monkeypatch.setattr(prov, "_BUNDLES_DIR", tmp_path / "bundles")
    assert "broken" in prov.discover_types()
    with pytest.raises(prov.BundleError, match="default_facet"):
        prov.validate_bundle("broken")


def test_list_valid_types_returns_metadata():
    """P3 : `list_valid_types` = LA source des types OFFERTS (dropdown UI + choices CLI). Chaque type
    découvert **valide** y figure avec ses métadonnées de manifeste ; browser-game porte ses 4 facettes."""
    valid = list_valid_types()
    by_type = {e["type"]: e for e in valid}
    assert set(by_type) == set(discover_types())        # tous les bundles vendorés valides → tous offerts
    for e in valid:
        assert e["version"], f"{e['type']} : version absente de l'entrée"
        assert e["default_facet"] in e["facets"]
    bg = by_type["browser-game"]
    assert bg["version"] == "1"
    assert bg["default_facet"] == "backend"
    assert set(bg["facets"]) == {"frontend", "backend", "game-design", "doc"}
    # Déclaration MCP sèche surfacée par entrée : browser-game pointe son silo tech dédié.
    assert bg["mcp"] == {"corpus": True, "tech_scope": "browser-game"}
    assert by_type["generic"]["mcp"] == {"corpus": True}   # universel, sans silo type-spécifique


def test_bundle_manifest_declares_mcp_corpus():
    """DoD 1 : le manifeste porte une déclaration MCP **sèche** (jamais de secret ni d'endpoint) — `base`
    (generic) marque l'universel `corpus = true` ; browser-game ajoute son `tech_scope`."""
    assert read_bundle_manifest("generic")["mcp"] == {"corpus": True}
    assert read_bundle_manifest("browser-game")["mcp"] == {"corpus": True, "tech_scope": "browser-game"}


def test_validate_bundle_rejects_malformed_mcp(tmp_path, monkeypatch):
    """Le bloc `[bundle.mcp]` est optionnel mais **typé fail-closed** : `corpus` non-booléen → refus."""
    import cockpit.provision as prov
    meta = tmp_path / "bundles" / "base" / ".cockpit"
    meta.mkdir(parents=True)
    (meta / "bundle.toml").write_text(
        '[bundle]\nversion = "1"\nproject_type = "generic"\nfacets = ["doc"]\ndefault_facet = "doc"\n'
        '[bundle.mcp]\ncorpus = "yes"\n',                  # str au lieu de bool → invalide
        encoding="utf-8")
    doc = tmp_path / "bundles" / "base" / ".claude" / "facets" / "doc"
    doc.mkdir(parents=True)
    (doc / "PERSONA.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(prov, "_BUNDLES_DIR", tmp_path / "bundles")
    with pytest.raises(prov.BundleError, match="corpus"):
        prov.validate_bundle("generic")


def test_list_valid_types_excludes_broken(tmp_path, monkeypatch):
    """Fail-closed : un overlay cassé (default_facet hors facets) est DÉCOUVERT mais **écarté** de
    `list_valid_types` — on n'offre jamais un type qu'on ne saurait pas semer. Le valide reste offert."""
    import cockpit.provision as prov
    meta = tmp_path / "bundles" / "base" / ".cockpit"
    meta.mkdir(parents=True)
    (meta / "bundle.toml").write_text(
        '[bundle]\nversion = "1"\nproject_type = "generic"\nfacets = ["doc"]\ndefault_facet = "doc"\n',
        encoding="utf-8")
    doc = tmp_path / "bundles" / "base" / ".claude" / "facets" / "doc"
    doc.mkdir(parents=True)
    (doc / "PERSONA.md").write_text("x", encoding="utf-8")
    broken = tmp_path / "bundles" / "types" / "broken" / ".cockpit"
    broken.mkdir(parents=True)
    (broken / "bundle.toml").write_text(
        '[bundle]\nversion = "1"\nproject_type = "broken"\nfacets = ["doc"]\ndefault_facet = "nope"\n',
        encoding="utf-8")
    monkeypatch.setattr(prov, "_BUNDLES_DIR", tmp_path / "bundles")
    offered = {e["type"] for e in prov.list_valid_types()}
    assert "broken" in prov.discover_types()            # découvert par le filesystem...
    assert "broken" not in offered                      # ...mais pas offert (validation fail-closed)
    assert "generic" in offered                         # le type valide reste offert


def test_browser_game_wires_game_dev_identity():
    """P2 : le type `browser-game` sème une identité game-dev complète + outils câblés par référence.
    Contrat de la phase (void-runner créable de zéro) : cadre verrouillé dans le CLAUDE.md, foyer de design
    (idea_home), engine code-map épinglé ts, et les 4 facettes de dispatch adossées."""
    bundle = load_bundle("browser-game")
    # (a) identité : le CLAUDE.md porte le cadre VERROUILLÉ de l'archétype (serveur-autoritatif, stack TS).
    claude = bundle["CLAUDE.md"]
    for marker in ("serveur-autoritatif", "browser-game", "Hono", "Vite", "VERROUILLÉ"):
        assert marker in claude, f"CLAUDE.md sans marqueur « {marker} »"
    # (b) idea_home : la conception vit dans docs/design.md (jamais inlinée dans le CLAUDE.md).
    assert "docs/design.md" in bundle and bundle["docs/design.md"].strip()
    assert "idea_home" in bundle["docs/design.md"]
    # (c) câblage d'outil par référence : code-map épinglé à l'engine ts (correct dès l'amorçage vide).
    assert 'select = "ts"' in bundle[".codemap.toml"]
    # (d) les 4 facettes de dispatch sont adossées (frontend/backend/game-design de l'overlay, doc de base).
    manifest = read_bundle_manifest("browser-game")
    assert manifest["default_facet"] == "backend"
    assert set(manifest["facets"]) == {"frontend", "backend", "game-design", "doc"}
    # (e) fidélité de la bulle game-design NO-CODE : ses permissions n'ouvrent AUCUN Bash de build/test
    #     (sinon la posture « imaginer » dérive en « coder »).
    gd_settings = bundle[".claude/facets/game-design/settings.local.json"]
    for forbidden in ("npm:", "node:", "tsc:", "vitest:", "eslint:"):
        assert forbidden not in gd_settings, f"game-design ne doit pas permettre Bash({forbidden}…)"


def test_load_bundle_is_deterministic():
    for t in discover_types():
        assert load_bundle(t) == load_bundle(t)     # lecture triée + merge `|` déterministe


def test_launch_roadmap_generic_is_socle_with_acceptance():
    rm = load_launch_roadmap("generic")
    feats = {f["slug"]: f for f in rm["features"]}
    assert set(feats) == {"socle"} and feats["socle"]["facet"] == "doc"
    tasks = {t["slug"]: t for t in feats["socle"]["tasks"]}
    assert tasks["decompose"]["depends_on"] == ["cadrage"]          # ordre porté par la graine
    assert all(t.get("acceptance") for t in tasks.values())        # DoD binaire partout


def test_launch_roadmap_browser_game_overrides_whole_file():
    rm = load_launch_roadmap("browser-game")
    feats = {f["slug"]: f for f in rm["features"]}
    assert set(feats) == {"socle-design"}                          # remplace le socle générique, pas fusionné
    assert feats["socle-design"]["facet"] == "game-design"
    assert {t["slug"] for t in feats["socle-design"]["tasks"]} == {"interview", "boucle-eco", "decompose"}


def test_launch_roadmap_absent_type_is_failsoft(tmp_path, monkeypatch):
    # un type sans .cockpit/launch-roadmap.yaml → {} (aucun seed), jamais un crash
    monkeypatch.setattr("cockpit.provision.load_bundle", lambda *_a, **_k: {"CLAUDE.md": "x"})
    assert load_launch_roadmap("whatever") == {}


def test_launch_roadmap_is_deterministic():
    for t in discover_types():
        assert load_launch_roadmap(t) == load_launch_roadmap(t)


def test_overlay_adds_facets_and_overrides_whole_file():
    base = load_bundle("generic")
    svc = load_bundle("service-api")
    # (a) l'overlay AJOUTE sa facette (fichier overlay-only)
    assert ".claude/facets/backend/PERSONA.md" in svc
    assert ".claude/facets/backend/PERSONA.md" not in base
    # (b) la facette `doc` de base est CONSERVÉE (union, pas remplacement de dossier)
    assert ".claude/facets/doc/PERSONA.md" in svc
    # (c) SURCHARGE whole-file : docs/architecture.md, .cockpit/bundle.toml ET CLAUDE.md diffèrent de base
    assert svc["docs/architecture.md"] != base["docs/architecture.md"]
    assert "service / API" in svc["docs/architecture.md"]
    assert 'project_type = "service-api"' in svc[".cockpit/bundle.toml"]
    assert svc["CLAUDE.md"] != base["CLAUDE.md"]                # CLAUDE.md est spécialisé par type
    assert "ingénieur backend senior Python" in svc["CLAUDE.md"]
    # (d) les skills (contrat commun) restent ceux de base (non dupliqués par l'overlay)
    assert ".claude/skills/work-loop/SKILL.md" in svc


@pytest.mark.parametrize("project_type", _OVERLAY_TYPES)
def test_declared_facets_have_backing_dirs(project_type):
    """Cohérence : chaque facette déclarée dans `.cockpit/bundle.toml` a un dossier `.claude/facets/<f>/`
    avec PERSONA.md/METHOD.md/settings.local.json dans le bundle composé. `default_facet` ∈ facets."""
    bundle = load_bundle(project_type)
    manifest = tomllib.loads(bundle[".cockpit/bundle.toml"])["bundle"]
    assert manifest["project_type"] == project_type
    assert manifest["default_facet"] in manifest["facets"]
    for fac in manifest["facets"]:
        for leaf in ("PERSONA.md", "METHOD.md", "settings.local.json"):
            key = f".claude/facets/{fac}/{leaf}"
            assert key in bundle, f"{project_type} : facette {fac} déclarée sans {key}"
            assert bundle[key].strip(), f"{project_type} : {key} vide"


# Fichiers de code REPRÉSENTATIFS de la stack de chaque type (ce qu'un worker toucherait) → chaque groupe
# toolchain qu'ils déclenchent DOIT monter dans le seed. service-api/cli-tool = Python ; front-ts/browser-game
# = TypeScript unifié (node hors web/ → backend-node ; web/ → front).
_TYPE_TOOLCHAIN_PROBES = {
    "service-api": ["probe.py"],
    "cli-tool": ["probe.py"],
    "front-ts": ["server.mjs", "web/Probe.tsx"],
    "browser-game": ["src/probe.ts", "web/Probe.tsx"],
}


@pytest.mark.parametrize("project_type", sorted(_TYPE_TOOLCHAIN_PROBES))
def test_typed_seed_ships_mountable_toolchain(project_type: str, tmp_path: Path):
    """**Garde-fou anti-récidive** : un bundle typé doit satisfaire sa PROPRE gate Tier-0. Pour chaque langage
    que sa stack implique, le groupe toolchain déclenché doit **MONTER** (manifeste `pyproject.toml` /
    `package.json`+`gate` présent dans le seed) — sinon tout worker touchant ce code tombe sur « toolchain non
    montable » : échec par CONSTRUCTION (le trou trouvé au dogfood 2026-07-16, cher, alors qu'il était lisible
    statiquement). Ce test le rend visible à chaque `pytest`, jamais plus à un E2E live."""
    from cockpit.gate import toolchain
    wt = tmp_path / project_type
    for rel, content in load_bundle(project_type).items():
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    triggered = toolchain.applicable_triggers(_TYPE_TOOLCHAIN_PROBES[project_type])
    assert triggered, f"{project_type} : aucune probe ne déclenche de groupe (table de test à revoir)"
    for group in triggered:
        assert toolchain._steps_for(group, wt) is not None, (
            f"{project_type} : le seed déclenche le groupe {group!r} mais sa toolchain n'est PAS montable "
            f"(manifeste absent) → un worker échouerait le gate Tier-0 par construction")


def test_browser_game_seeds_runnable_ts_mono_skeleton():
    """DoD `cockpit-bundle-seeds-runnable-scaffold` : le seed browser-game porte un squelette TS-mono
    **runnable né-avec** — client `web/` (Vite/React), serveur `server/` (Hono), modèle Zod partagé + son
    test Vitest, gate `tsc → vitest`. On prouve la PRÉSENCE + la montabilité statique du squelette ; le vert
    npm réel (`npm install && npm run dev` + `vitest`) se prouve sur env node, hors de ce gate."""
    bundle = load_bundle("browser-game")
    expected = ("package.json", "tsconfig.json", "vite.config.ts", "vitest.config.ts", "eslint.config.js",
                "src/shared/schema.ts", "src/shared/schema.test.ts", "server/index.ts",
                "web/index.html", "web/main.tsx", "web/App.tsx", "web/index.css", "web/queryClient.ts")
    for rel in expected:
        assert rel in bundle and bundle[rel].strip(), f"squelette runnable : {rel} absent/vide du seed"
    pkg = json.loads(bundle["package.json"])
    gate = pkg["scripts"]["gate"]
    assert "eslint" in gate and "tsc" in gate and "vitest" in gate   # gate réel = eslint → tsc → vitest
    assert "react" in pkg["dependencies"] and "hono" in pkg["dependencies"]   # univers unifié web + server
    assert "@tanstack/react-query" in pkg["dependencies"]        # poll temps-réel semé né-avec (réconcilié)
    assert "tailwindcss" in pkg["devDependencies"]               # UI de gestion dense : Tailwind semé né-avec
    assert "{{game_name}}" in bundle["src/index.ts"]            # jeton de mission laissé (seed verbatim)
    for rel in ("web/App.tsx", "web/index.html"):               # genericité : aucun slug figé côté client
        assert "cockpit-" not in bundle[rel] and "demo" not in bundle[rel].lower()


_BASE_CROSS_FACETS = ("code", "test", "infra", "review")


def test_generic_exposes_cross_cutting_facets_not_just_doc():
    """Un projet `generic` (base seule) porte les facettes cross-cutting code/test/infra/review — pas
    seulement `doc` : déclarées au bundle.toml, dossiers backing complets, bundle valide. → un generic peut
    dispatcher du code (ou test/infra/review), plus une persona « Doc » pour tout travail."""
    bundle = load_bundle("generic")
    manifest = tomllib.loads(bundle[".cockpit/bundle.toml"])["bundle"]
    for fac in ("doc", *_BASE_CROSS_FACETS):
        assert fac in manifest["facets"], f"generic ne déclare pas la facette {fac!r}"
        for leaf in ("PERSONA.md", "METHOD.md", "settings.local.json"):
            key = f".claude/facets/{fac}/{leaf}"
            assert key in bundle and bundle[key].strip(), f"generic : {key} absent/vide"
    validate_bundle("generic")                    # ne lève pas : default_facet ∈ facets, dossiers présents


def test_orchestrator_role_facet_carries_loop_verbs_not_write():
    """Le rôle **orchestrateur** (boucle `claude -p` dispatch→gate→merge) est semé dans base : allow-list
    scopée aux VERBES DU LOOP (`cockpit dispatch/gate/merge/run`), SANS Write/Edit (il pilote, les workers
    écrivent). Rôle, pas facette worker → **non déclaré** au vocab du bundle (pas dispatchable comme feature).
    Ne widen PAS le terminal humain (`.claude/settings.json`, qui n'a jamais les verbes cockpit)."""
    bundle = load_bundle("generic")
    allow = json.loads(bundle[".claude/facets/orchestrator/settings.local.json"])["permissions"]["allow"]
    assert any("cockpit dispatch" in a for a in allow) and any("cockpit merge" in a for a in allow)
    assert any("cockpit gate" in a for a in allow)
    assert "Write" not in allow and "Edit" not in allow           # pilote, ne code pas
    manifest = tomllib.loads(bundle[".cockpit/bundle.toml"])["bundle"]
    assert "orchestrator" not in manifest["facets"]               # rôle, pas facette worker déclarée
    human = json.loads(bundle[".claude/settings.json"])["permissions"]["allow"]
    assert not any("cockpit dispatch" in a for a in human)        # terminal humain NON élargi


def test_generic_project_can_resolve_and_activate_code_facet(tmp_path: Path):
    """Preuve DoD : une feature de projet `generic` pose `facet='code'` → résolue + activée (le dossier de
    facette est hérité de base). Le worker « code » n'est donc pas réservé aux overlays de type."""
    key = ".claude/facets/code/settings.local.json"
    settings = load_bundle("generic")[key]
    dst = tmp_path / key
    dst.parent.mkdir(parents=True)
    dst.write_text(settings, encoding="utf-8")
    assert facet.resolve_facet(tmp_path, "code") == "code"        # facette explicite l'emporte
    written = facet.activate_facet(tmp_path, "code")              # copie settings.local.json dans la worktree
    assert written is not None
    assert Path(written).read_text(encoding="utf-8") == settings


# Grille canonique d'un CLAUDE.md (structure de référence, cf. brief bosse 2026-07-03) : 6 sections fixes.
_CLAUDE_SECTIONS = (
    "## 1. Contexte et objectifs",
    "## 2. Rôle de l'IA (persona)",
    "## 3. Stack technique et environnement",
    "## 4. Règles de code et conventions",
    "## 5. Format des réponses attendues",
    "## 6. Workflows et processus",
)


@pytest.mark.parametrize("project_type", discover_types())
def test_claude_md_follows_canonical_six_sections(project_type):
    """Tout CLAUDE.md semé (base ⊕ overlay) suit la grille en 6 sections : contexte, persona, stack,
    conventions, format des réponses, workflows. Chaque section porte de la substance (persona + gate)."""
    claude = load_bundle(project_type)["CLAUDE.md"]
    for section in _CLAUDE_SECTIONS:
        assert section in claude, f"{project_type} : CLAUDE.md sans section « {section} »"
    assert "persona" in claude.lower()                          # §2 nomme une posture
    assert "GO humain" in claude                                # §4/§6 : fail-closed explicite
    assert "docsmap where" in claude                            # anti-archéologie ancrée


def test_methodology_skills_present_in_every_bundle():
    """Phase 6 : les deux skills de méthodo sont dans la base → présents et non vides dans CHAQUE type
    (base ⊕ overlay), et le CLAUDE.md socle les référence (une session doit pouvoir les découvrir)."""
    for t in discover_types():
        bundle = load_bundle(t)
        for skill in _METHOD_SKILLS:
            assert skill in bundle, f"{t} : skill méthodo manquant {skill}"
            assert bundle[skill].strip(), f"{t} : {skill} vide"
    base = load_bundle("generic")
    assert "roadmap-decompose" in base["CLAUDE.md"] and "docs-authoring" in base["CLAUDE.md"]


@pytest.mark.parametrize("project_type", _OVERLAY_TYPES)
def test_type_architecture_is_non_stub(project_type):
    """Chaque type surcharge `docs/architecture.md` avec une vraie doc de type (pas le stub générique) :
    section « Comment ce projet se travaille » présente et le stub « À renseigner » cantonné à l'Intention."""
    arch = load_bundle(project_type)["docs/architecture.md"]
    assert "Comment ce projet se travaille" in arch
    assert arch != load_bundle("generic")["docs/architecture.md"]      # bien une surcharge de type
    assert arch.count("À renseigner") <= 1                             # au plus l'Intention reste un gabarit


@pytest.mark.parametrize("project_type", discover_types())
def test_bundle_seeds_per_directory_readmes(project_type: str, tmp_path: Path):
    """Un projet naît avec un ancrage doc par-dossier : un `README.md` racine + un par dossier structurant
    (`docs/`, `.claude/`, via la base → tout type), matérialisés VERBATIM dans l'arbre semé. Générique :
    aucun slug de projet en dur (le seed est réutilisable à l'identique). Le squelette RUNNABLE reste une
    autre task ; ici = ancrage doc uniquement."""
    bundle = load_bundle(project_type)
    wt = tmp_path / project_type
    for rel, content in bundle.items():             # matérialise le seed → prouve que les README atterrissent
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for rel in _BASE_READMES:
        assert (wt / rel).is_file(), f"{project_type} : README par-dossier absent de l'arbre semé : {rel}"
        body = (wt / rel).read_text(encoding="utf-8")
        assert body.strip(), f"{project_type} : {rel} vide"
        assert "cockpit-" not in body and "demo" not in body.lower(), (
            f"{project_type} : {rel} porte un slug/exemple figé (le seed doit rester générique)")


@pytest.mark.parametrize("project_type", _SRC_README_TYPES)
def test_src_bearing_types_seed_src_readme(project_type: str):
    """Les types qui portent une arbo `src/` (browser-game, front-ts) sèment aussi `src/README.md` — l'entrée
    du code est ancrée comme les autres dossiers structurants."""
    bundle = load_bundle(project_type)
    assert "src/README.md" in bundle, f"{project_type} : src/README.md non semé"
    assert bundle["src/README.md"].strip(), f"{project_type} : src/README.md vide"


def test_facet_settings_local_are_seeded_files_not_ignored():
    # Les settings.local.json SOURCES vivent sous .claude/facets/**/ → vendorés (le walk les inclut).
    # Le .gitignore de base n'ignore que la COPIE activée `.claude/settings.local.json` (enfant direct) :
    # le motif `.claude/*.local.json` ne traverse pas `/`, donc les sources nichées survivent.
    svc = load_bundle("service-api")
    assert ".claude/facets/backend/settings.local.json" in svc
    gitignore = svc[".gitignore"]
    assert ".claude/settings.local.json" in gitignore        # la copie activée (Phase 3) est ignorée
    assert ".claude/facets/" not in gitignore                # les sources de facette ne sont PAS ignorées


# -- facet : résolution + activation (Phase 3) ------------------------------------------------------

def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_resolve_facet_explicit_then_default_then_fallback(tmp_path: Path):
    _write(tmp_path / ".cockpit" / "bundle.toml",
           '[bundle]\nproject_type = "service-api"\nfacets = ["backend", "doc"]\ndefault_facet = "backend"\n')
    assert facet.resolve_facet(tmp_path, "frontend") == "frontend"   # feature.facet explicite l'emporte
    assert facet.resolve_facet(tmp_path, None) == "backend"          # sinon default_facet du bundle.toml
    assert facet.resolve_facet(tmp_path / "vide", None) == "doc"     # ni l'un ni l'autre → fallback doc


def test_activate_facet_copies_settings_local_and_is_idempotent(tmp_path: Path):
    _write(tmp_path / ".claude" / "facets" / "backend" / "settings.local.json", '{"marker": "backend"}')
    written = facet.activate_facet(tmp_path, "backend")
    activated = tmp_path / ".claude" / "settings.local.json"
    assert written == str(activated) and activated.is_file()
    assert "backend" in activated.read_text(encoding="utf-8")
    assert facet.activate_facet(tmp_path, "backend") == str(activated)   # idempotent (overwrite)


def test_activate_facet_failsoft_when_no_settings_local(tmp_path: Path):
    # facette sans settings.local.json (ex. `doc` minimal) → None, aucune écriture (pas de crash)
    (tmp_path / ".claude" / "facets" / "doc").mkdir(parents=True)
    assert facet.activate_facet(tmp_path, "doc") is None
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


# -- P3 : config de run semée (types-services déployables sans édition) -----------------------------

# Les types-SERVICE portent une config de run (compose + Dockerfile + stub runnable) ; les autres non
# (un CLI / un projet générique n'expose aucun service long-running à héberger).
_SERVICE_TYPES = ("service-api", "front-ts", "browser-game")
# `generic`/`cli-tool` n'exposent aucun service long-running à héberger → aucune run config semée (l'engine
# refusera proprement leur deploy). `browser-game` est désormais déployable : la chaîne deploy+E2E « jouable »
# exige que son SoT porte compose.yaml + Dockerfile dès l'amorçage (deploy chain).
_NON_SERVICE_TYPES = ("generic", "cli-tool")
# stub runnable par type-service. browser-game : entrypoint prod qui réunifie le front statique (Vite → dist/)
# et l'API Hono derrière un unique port 8000 (server/prod.ts, pas un stub racine — univers TS unifié).
_APP_STUB = {"service-api": "app.py", "front-ts": "server.mjs", "browser-game": "server/prod.ts"}


@pytest.mark.parametrize("project_type", _SERVICE_TYPES)
def test_service_type_seeds_deployable_run_config(project_type):
    """Un type-service sème, à la RACINE de l'arbre, la config de run complète : compose.yaml + Dockerfile
    + stub d'app runnable + .dockerignore → un projet frais est déployable SANS édition manuelle (P3)."""
    bundle = load_bundle(project_type)
    for f in ("compose.yaml", "Dockerfile", ".dockerignore", _APP_STUB[project_type]):
        assert f in bundle, f"{project_type} : {f} non semé à la racine"
        assert bundle[f].strip(), f"{project_type} : {f} vide"


@pytest.mark.parametrize("project_type", _NON_SERVICE_TYPES)
def test_non_service_type_seeds_no_run_config(project_type):
    """`generic`/`cli-tool` ne sèment NI compose NI Dockerfile : l'engine refusera proprement leur deploy
    (pas de faux service). La non-déployabilité est une propriété assumée, pas un oubli."""
    bundle = load_bundle(project_type)
    assert "compose.yaml" not in bundle
    assert "Dockerfile" not in bundle


@pytest.mark.parametrize("project_type", _SERVICE_TYPES)
def test_seeded_compose_builds_from_repo_and_fails_loud_on_missing_port(project_type):
    """Le compose semé est un YAML valide qui BUILD depuis le repo (`build:`, pas `image:` en dur) et publie
    le port INJECTÉ avec interpolation fail-loud `${COCKPIT_PORT:?...}` — jamais un port figé, jamais de
    faux-vert si le cockpit n'injecte pas le port."""
    compose_text = load_bundle(project_type)["compose.yaml"]
    doc = yaml.safe_load(compose_text)
    web = doc["services"]["web"]
    assert web["build"] == "."                               # build depuis l'arbre, Dockerfile canonique
    assert "image" not in web                                # jamais une image figée (≠ smoke P2)
    assert "${COCKPIT_PORT:?" in compose_text                # interpolation fail-loud (doc compose 0103)
    assert ":8000" in "".join(web["ports"])                  # port interne du contrat (stub écoute 8000)


@pytest.mark.parametrize("project_type", _SERVICE_TYPES)
def test_seeded_compose_has_no_host_bind_or_shared_network(project_type):
    """Anti-pollution P4 (frontière FS/réseau structurelle) : le compose semé ne déclare NI `volumes:` NI
    `networks:` — ni au niveau top-level ni sur le service. Donc aucun bind vers le FS hôte (le service ne
    voit que son image, buildée depuis SON arbre) et un réseau podman **par compose-project** (jamais un
    réseau partagé où A joindrait B). L'isolation est une propriété du payload, pas une config à ajouter."""
    doc = yaml.safe_load(load_bundle(project_type)["compose.yaml"])
    assert "volumes" not in doc and "networks" not in doc     # rien de partagé au top-level
    web = doc["services"]["web"]
    assert "volumes" not in web                               # aucun bind/volume monté dans le conteneur
    assert "networks" not in web                              # réseau par défaut = isolé par compose-project


@pytest.mark.parametrize("project_type", _SERVICE_TYPES)
def test_seeded_run_config_is_generic_no_hardcoded_slug(project_type):
    """Invariant de genericité du payload : le stub d'app et le compose ne portent AUCUN nom de projet en
    dur (le payload est réutilisable à l'identique pour tout projet du type)."""
    bundle = load_bundle(project_type)
    for f in ("compose.yaml", "Dockerfile", _APP_STUB[project_type]):
        assert "cockpit-" not in bundle[f]                   # pas de compose-project/namespace figé
        assert "demo" not in bundle[f].lower()               # aucun slug d'exemple figé
