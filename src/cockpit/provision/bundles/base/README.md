# README — projet semé par le cockpit

Ce dépôt a été **provisionné par le cockpit** : il naît avec un toolkit qui le rend **travaillable seul**
(un clone suffit) — le cockpit ne fait qu'**automatiser** la même boucle, aux mêmes invariants.

## Par où entrer

- **`CLAUDE.md`** (racine) — contexte global + instructions système lues au début de chaque session. Le
  détail (intention, architecture, décisions) vit dans `docs/`, pas ici.
- **`docs/`** — la mémoire durable du projet (intention, architecture, specs, décisions). Interroge-la par
  intention : `docsmap where "<intention>"` plutôt que tout relire. Voir `docs/README.md`.
- **`.claude/`** — le toolkit agentic : personas dispatchables (`facets/`) et boucles outillées (`skills/`).
  Voir `.claude/README.md`.

## Comment ce projet se travaille

1. **Planifie** avec le skill `roadmap-decompose` : l'intention devient des features (chacune taguée d'une
   facette) et des tasks (DAG `depends_on` + critères d'`acceptance`).
2. **Exécute** chaque feature via `work-loop` : worktree `feature/<sujet>` depuis `dev` → `quality-gate` vert
   → `dev` en ff-only → `main` promu depuis un `dev` vert. `main` ne se travaille jamais ; tout acte
   irréversible exige un **GO humain** (fail-closed).
3. **Mémorise** avec `docs-authoring` : ce qui est décidé/construit et doit survivre va dans `docs/`.

## Cartes du repo

`codemap` (code) · `docsmap` (prose) · `frontmap` (UI) — config committée, binaires fournis par
l'environnement. Bâtis-les une fois puis interroge par intention (anti-archéologie : interroge la carte avant
de `grep`/lire en bloc).
