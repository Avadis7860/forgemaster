"""prompt — synthétiseur **déterministe** du prompt d'un worker `claude -p`. Transforme la NEXT task
(résolue par `resolver`) en prompt de prise en charge, cadré par le contexte **du projet lui-même**.

Port du **PATTERN** de `lib/plan_prompt.py` (gabarit générique + `PROJECT_*_HOME` : le prompt puise ses
injections dans le repo du projet, rien codé en dur par projet). On **écarte délibérément** les injections
propres à la couche *mémoire du vault* (blueprints, stacks, catalogs central, zone-memory, profils de
spécialité) : elles n'ont pas de sens pour une forge générique, dont chaque projet porte son propre `docs/`.
En revanche le **capital distillé PROPRE au projet** (`docs/decisions/`, écrit par un run passé via
`worker.write_decision_doc`) EST relu ici (`_decisions_block`) : c'est le cliquet de compounding
projet-local — sans quoi la forge écrirait un minerai qu'elle ne relit jamais. De même, la **cible visuelle**
propre au projet (`docs/design/<slug>/brief.md`, écrite par `design.apply_template` quand le dirigeant
applique un template UI de référence) EST relue (`_design_block`) : le worker s'en inspire et la customise au
lieu de coder l'UI en aveugle. Déterministe (zéro LLM au build, zéro I/O réseau) — seul un `read_text` des
docs présents du worktree."""
from __future__ import annotations

from pathlib import Path

from cockpit.provision import facet as facet_mod

# Docs de contexte in-repo (pattern `PROJECT_*_HOME`). Absents ⇒ simplement non pointés (fail-soft).
CONTEXT_DOCS: tuple[tuple[str, str], ...] = (
    ("intention produit", "docs/design.md"),
    ("roadmap", "docs/roadmap.yaml"),
    ("architecture", "docs/architecture.md"),
)
_EXCERPT_MAX = 1200   # caractères d'aperçu par doc (le worker lit le fichier entier au besoin)
_DECISIONS_BUDGET = 2400     # budget total de caractères du capital distillé relu (borne anti-explosion)
_DECISION_EXCERPT_MAX = 700  # aperçu par décision (le worker lit le fichier complet au besoin)
_DESIGN_BUDGET = 2000        # budget total du bloc « cible visuelle » (templates appliqués — anti-explosion)
_DESIGN_EXCERPT_MAX = 900    # aperçu par template appliqué (le worker lit brief + tokens/preview au besoin)


def _hygiene_note() -> str:
    """Consignes d'HYGIÈNE partagées worker/fix (DRY) : (1) worktree propre au gate — nettoyer le scratch
    (`rm` de tes fichiers temporaires est AUTORISÉ et attendu, aucun `_preview.tsx`/temp résiduel) ; (2)
    preuve de rendu UI **sans navigateur** — rendu HTML statique via le renderer du projet, pas de page de
    preview scaffoldée ni de dev-server. Résout les frictions de la boucle headless (permissions scratch +
    tours gaspillés à bricoler un preview)."""
    return (
        "HYGIÈNE. Nettoie tes fichiers temporaires avant de finir : le worktree doit être PROPRE au gate "
        "(aucun scratch/preview résiduel) — le `rm` de tes fichiers de travail temporaires est autorisé et "
        "attendu. Si ta task produit un composant ou un écran d'UI, NE scaffolde PAS de page `_preview.tsx` "
        "ni ne lance de dev-server pour te vérifier : rends le composant en **HTML statique** via le "
        "renderer du projet (React : `renderToStaticMarkup` de `react-dom/server`) via un mini-entry lancé "
        "par la toolchain du projet (`npx tsx <entry>`, ou l'équivalent de ton stack), inspecte le HTML "
        "produit, puis supprime l'entry — preuve de rendu en 1-2 tours, sans navigateur. Si ton projet n'a "
        "pas de renderer SSR, dis-le et appuie-toi sur le type-check (`tsc --noEmit`)."
    )


def _mandate() -> str:
    """Mandat autonome générique (adapté de `_code_autonomous_mandate`) : le worker implémente la task dans
    son worktree, sans piloter le cycle git (branche/commit/push = la machinerie de dispatch s'en charge)."""
    return (
        "Tu es un worker autonome dispatché sur UNE task précise, dans un worktree git isolé (ta branche "
        "est déjà créée et checkout). Implémente la task de bout en bout : lis le contexte du repo, écris "
        "le code et les tests, vérifie qu'ils passent. Travaille SANS poser de question (tu tournes en "
        "headless : aucun interlocuteur). Le projet porte sa propre doc dans `docs/` — interroge-la avec "
        "`docsmap where \"<intention>\"` (→ fichier:lignes de la section pertinente) plutôt que de tout lire "
        "en bloc. NE touche PAS au cycle git (pas de branch/commit/push) — la forge s'en charge après ton "
        "run. Reste STRICTEMENT dans le périmètre de la task ; ne déborde pas. " + _hygiene_note() + " "
        "TERMINE ton message final par une section `## Décisions prises` : les choix que tu as retenus, "
        "les alternatives que tu as écartées (et pourquoi), et les contraintes que tu as découvertes. La "
        "forge récolte ce bloc en minerai durable (`docs/decisions/`) — sois concret, pas de remplissage."
    )


def _context_block(root: Path) -> str:
    """Bloc « contexte du projet » : pour chaque doc présent, un aperçu borné + le chemin à lire en entier."""
    lines: list[str] = []
    for label, rel in CONTEXT_DOCS:
        doc = root / rel
        if not doc.is_file():
            continue
        try:
            text = doc.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        excerpt = text[:_EXCERPT_MAX] + ("…" if len(text) > _EXCERPT_MAX else "")
        lines.append(f"### {label} — `{rel}` (lis le fichier complet au besoin)\n{excerpt}")
    if not lines:
        return ("### contexte projet\n(aucun `docs/` de contexte dans ce repo — "
                "appuie-toi sur le code existant.)")
    return "\n\n".join(lines)


def _decisions_block(root: Path) -> str:
    """Bloc « capital distillé du projet » : relit `docs/decisions/` — le minerai qu'un run PASSÉ a distillé
    (`worker.write_decision_doc`) — et le ré-injecte borné, pour fermer la boucle de compounding
    projet-local. Sans lui, le cockpit **écrivait un capital qu'il ne relisait jamais** → pas d'effet
    cliquet. Sélection **déterministe par récence** : les `.md` sont datés en tête de nom
    (`<date>--<slug>.md`, cf. `worker.write_decision_doc`), triés décroissant (le plus frais d'abord) ; on
    accumule des aperçus bornés jusqu'à `_DECISIONS_BUDGET`, chaque décision porte son chemin (le worker lit
    le fichier entier / `docsmap where` au besoin). Absent/vide ⇒ `""` (fail-soft, filtré comme un facet
    absent). PUR (tri par nom → aucune horloge, testable)."""
    decisions_dir = root / "docs" / "decisions"
    if not decisions_dir.is_dir():
        return ""
    docs = sorted((p for p in decisions_dir.glob("*.md") if p.is_file()),
                  key=lambda p: p.name, reverse=True)
    lines: list[str] = []
    budget = _DECISIONS_BUDGET
    for doc in docs:
        if budget <= 0:   # budget épuisé et il reste des décisions plus anciennes → pointeur, pas de corps
            lines.append("- … (`docs/decisions/` porte d'autres décisions plus anciennes — "
                         "`docsmap where \"<intention>\"` pour les retrouver)")
            break
        try:
            text = doc.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        excerpt = text[:_DECISION_EXCERPT_MAX] + ("…" if len(text) > _DECISION_EXCERPT_MAX else "")
        rel = doc.relative_to(root)
        lines.append(f"### `{rel}` (lis le fichier complet au besoin)\n{excerpt}")
        budget -= len(excerpt)
    if not lines:
        return ""
    return ("## Capital distillé du projet (décisions d'un run passé — relis-les avant de (re)décider)\n"
            + "\n\n".join(lines))


def _design_block(root: Path) -> str:
    """Bloc « cible visuelle du projet » : relit les briefs des templates UI **appliqués** au projet
    (`docs/design/<slug>/brief.md`, posés par `design.apply_template` quand le dirigeant applique un template
    de référence de la vitrine) et les ré-injecte bornés. Chaque brief est une CIBLE d'inspiration — le worker
    s'en inspire et la **customise** au projet (jamais une copie verbatim), et lit les fichiers frères
    (`tokens.css` l'identité, `preview.png` le rendu-cible) au besoin. Symétrique de `_decisions_block` : côté
    ÉCRITURE `design.apply_template` sème le brief, côté LECTURE ce bloc le ré-injecte → le worker qui attaque
    l'UI voit « à quoi ça doit ressembler » au lieu de re-dériver une identité en aveugle. Sélection
    déterministe (tri par chemin), aperçus bornés jusqu'à `_DESIGN_BUDGET`. Absent/vide ⇒ `""` (fail-soft,
    filtré comme un facet absent). PUR (tri par nom → aucune horloge, testable)."""
    design_dir = root / "docs" / "design"
    if not design_dir.is_dir():
        return ""
    briefs = sorted((p for p in design_dir.glob("*/brief.md") if p.is_file()), key=str)
    lines: list[str] = []
    budget = _DESIGN_BUDGET
    for brief in briefs:
        if budget <= 0:   # budget épuisé, d'autres templates appliqués restent → pointeur, pas de corps
            lines.append("- … (`docs/design/` porte d'autres templates appliqués — lis leurs `brief.md`)")
            break
        try:
            text = brief.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        excerpt = text[:_DESIGN_EXCERPT_MAX] + ("…" if len(text) > _DESIGN_EXCERPT_MAX else "")
        rel = brief.relative_to(root)
        lines.append(f"### `{rel}` (lis le fichier complet + `tokens.css`/`preview.png` frères au besoin)\n"
                     + excerpt)
        budget -= len(excerpt)
    if not lines:
        return ""
    return ("## Cible visuelle du projet (template UI appliqué par le dirigeant — inspire-t'en et CUSTOMISE "
            "au projet, ne copie pas)\n" + "\n\n".join(lines))


def _facet_block(root: Path, facet: str, leaf: str) -> str:
    """Contenu d'un `.md` de facette (`PERSONA.md`/`METHOD.md`) — porte déjà son propre titre markdown.
    Absent/illisible ⇒ `""` (fail-soft : facette sans persona/méthode = simplement non injectée)."""
    doc = facet_mod.facet_dir(root, facet) / leaf
    if not doc.is_file():
        return ""
    try:
        return doc.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _acceptance_block(task: dict) -> str:
    """Les critères de DoD de la task, rendus verbatim. Absents ⇒ `""` (le mandat générique couvre alors
    « incrément complet et testé »)."""
    acc = (task.get("acceptance") or "").strip()
    return f"## Critères d'acceptation (DoD)\n{acc}" if acc else ""


def _fix_mandate() -> str:
    """Mandat d'une passe de CORRECTION (variante de `_mandate`) : le worker est dispatché sur la branche de
    feature (retour au dev) pour corriger les défauts d'un gate rouge, pas pour dérouler une task neuve."""
    return (
        "Tu es un worker autonome dispatché pour **corriger les défauts d'un gate rouge**, dans un worktree "
        "git isolé sur la branche de feature (« retour au dev » — ta branche est déjà créée et checkout). "
        "Corrige **exactement** les bloqueurs listés ci-dessous (findings 🔴 du reviewer et/ou étapes Tier-0 "
        "en échec) : lis le contexte, applique le fix minimal, vérifie qu'il passe. Travaille SANS poser de "
        "question (headless). Interroge la doc du repo avec `docsmap where \"<intention>\"`. NE touche PAS "
        "au cycle git (pas de branch/commit/push) — la forge committe ton travail après ton run. Reste "
        "STRICTEMENT dans le périmètre des défauts cités ; ne refactore pas au-delà. " + _hygiene_note() + " "
        "TERMINE ton message final par une section `## Décisions prises` : ce que tu as corrigé et pourquoi."
    )


def findings_block(findings: dict) -> str:
    """Rend les findings du gate rouge (le **brief = le verdict**) : les 🔴 du reviewer Tier-1 (file:line —
    claim + evidence) et les étapes Tier-0 natives en échec (cmd + exit + extrait d'erreur). Un verdict absent
    est simplement omis (fail-soft). Public : réutilisé par la forge pour PRÉFIXER le minerai d'une passe de
    fix (`worker.dispatch_fix` → `write_decision_doc`) du gate rouge d'origine."""
    lines: list[str] = []
    review = findings.get("review") or {}
    reds = [f for f in review.get("findings", []) if str(f.get("severity", "")).startswith("🔴")]
    if reds:
        lines.append("### 🔴 Findings reviewer (Tier-1) à corriger")
        for f in reds:
            loc = f"{f.get('file', '?')}:{f.get('line', '?')}"
            lines.append(f"- **{loc}** — {f.get('claim', '').strip()}")
            if f.get("evidence"):
                lines.append(f"  > {str(f['evidence']).strip()}")
    toolchain = findings.get("toolchain") or {}
    failed = [s for s in toolchain.get("steps", []) if not s.get("ok")]
    if failed:
        lines.append("### 🔴 Étapes Tier-0 (toolchain native) en échec")
        for s in failed:
            lines.append(f"- **{s.get('name', '?')}** : `{s.get('cmd', '?')}` (exit {s.get('exit_code')})")
            if s.get("error"):
                lines.append(f"  > {str(s['error']).strip()}")
    if not lines:
        return ("### bloqueurs\n(verdicts détaillés indisponibles — appuie-toi sur l'état du gate et le "
                "diff de la branche pour identifier les défauts.)")
    return "\n".join(lines)


def build_fix_prompt(project: dict, feature: dict, *, findings: dict, root: Path) -> str:
    """Compose le prompt d'une **passe de correction** (gate rouge → refix) : même cadrage facette
    (PERSONA/METHOD) + contexte in-repo que `build_worker_prompt`, mais le mandat est un fix ciblé et le corps
    porte les **findings** (le brief = le verdict). `findings` = `{review, toolchain}` (verdicts lus). PUR
    (hors lecture des fichiers présents). Part sur le **stdin** de `claude -p`."""
    root = Path(root)
    facet = facet_mod.resolve_facet(root, feature.get("facet"))
    header = (
        f"# Passe de correction — feature {feature['slug']} "
        f"({feature.get('title') or feature['slug']})\n"
        f"Projet : {project['slug']} ({project.get('name') or project['slug']}) · "
        f"Facette : {facet} · Branche : {feature.get('branch', '')}"
    )
    blocks = [
        header,
        _facet_block(root, facet, "PERSONA.md"),
        _fix_mandate(),
        _facet_block(root, facet, "METHOD.md"),
        f"## Bloqueurs du gate à corriger\n{findings_block(findings)}",
        f"## Contexte du projet\n{_context_block(root)}",
        _design_block(root),      # cible visuelle : template UI appliqué (customise, pas copie)
        _decisions_block(root),   # capital distillé d'un run passé (cliquet de compounding projet-local)
    ]
    return "\n\n".join(b for b in blocks if b) + "\n"


def build_worker_prompt(project: dict, feature: dict, task: dict, *, root: Path) -> str:
    """Compose le prompt worker à partir de la task NEXT, sa feature (dont la **facette**), son projet, et le
    contexte in-repo (`root` = le worktree). La facette injecte **persona + méthode** (lues des `.md`
    committés `.claude/facets/<f>/`) ; la task injecte ses **critères d'acceptation**. PUR (hors lecture des
    fichiers présents). Le prompt part sur le **stdin** de `claude -p` (jamais l'argv — parade E2BIG)."""
    root = Path(root)
    facet = facet_mod.resolve_facet(root, feature.get("facet"))
    header = (
        f"# Task : {task['slug']} — {task.get('title') or task['slug']} "
        f"(priorité {task.get('priority', 'P1')})\n"
        f"Projet : {project['slug']} ({project.get('name') or project['slug']}) · "
        f"Feature : {feature['slug']} ({feature.get('title') or feature['slug']}) · "
        f"Facette : {facet} · Branche : {feature.get('branch', '')}"
    )
    blocks = [
        header,
        _facet_block(root, facet, "PERSONA.md"),        # l'esprit à incarner pour ce type de travail
        _mandate(),
        _facet_block(root, facet, "METHOD.md"),         # la méthode de la facette
        _acceptance_block(task),                        # les critères requis (DoD)
        f"## Contexte du projet\n{_context_block(root)}",
        _design_block(root),      # cible visuelle : template UI appliqué (customise, pas copie)
        _decisions_block(root),   # capital distillé d'un run passé (cliquet de compounding projet-local)
    ]
    return "\n\n".join(b for b in blocks if b) + "\n"
