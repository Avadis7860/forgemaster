"""prompt — synthétiseur **déterministe** du prompt d'un worker `claude -p`. Transforme la NEXT task
(résolue par `resolver`) en prompt de prise en charge, cadré par le contexte **du projet lui-même**.

Port du **PATTERN** de `lib/plan_prompt.py` (gabarit générique + `PROJECT_*_HOME` : le prompt puise ses
injections dans le repo du projet, rien codé en dur par projet). On **écarte délibérément** les injections
propres à la couche *mémoire* du vault (blueprints, stacks, catalogs, décisions, zone-memory, profils de
spécialité) : elles n'ont pas de sens pour une forge générique, dont chaque projet porte son propre `docs/`.
Déterministe (zéro LLM au build, zéro I/O réseau) — seul un `read_text` des docs présents du worktree.
"""
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
        "run. Reste STRICTEMENT dans le périmètre de la task ; ne déborde pas. "
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
    ]
    return "\n\n".join(b for b in blocks if b) + "\n"
