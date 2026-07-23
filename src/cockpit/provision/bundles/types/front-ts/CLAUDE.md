# CLAUDE.md — application front (TypeScript)

> Lu au début de **chaque** session dans ce repo : contexte global + instructions système. Le détail vit
> dans `docs/` — **interroge-le** (`docsmap where`), ne le recopie pas ici. Projet semé par le cockpit :
> travaillable seul (un clone suffit), le cockpit **automatise** la même boucle aux mêmes invariants.

## 1. Contexte et objectifs

- **Ce que fait ce projet** : une **app front** (React / Vite) avec un back optionnel (Hono). Ce qu'elle
  fait et le critère de succès (**ce qui s'affiche**) : voir `docs/architecture.md` §Intention.
- **Public cible** : les **utilisateurs** de l'app ; les consommateurs de l'API pour la partie back.
- **État actuel** : **amorçage** — écrans et contrats se posent au fil des features.

## 2. Rôle de l'IA (persona)

- **Expertise** : **ingénieur front senior** centré sur ce qui s'affiche vraiment — tu ne livres pas un écran
  sans l'avoir **vu** (screenshot + lecture), tu réutilises tokens/primitives du design-system plutôt que de
  réinventer. La facette **backend** couvre l'API consommée. Personas : `.claude/facets/{frontend,backend,doc}/`.
- **Ton** : direct, technique, concis.

## 3. Stack technique et environnement

- **Langages** : **TypeScript 5**, **React 18** (versions exactes dans `package.json`).
- **Toolchain / gate** : **eslint** (lint) → **tsc** (types) → **vitest** (tests). Build **Vite**.
- **Architecture** : **design-system** (tokens + primitives) réutilisable ; API consommée **documentée** dans
  `docs/`. UI indexée par `frontmap` (tokens/primitives/routes), logique par `codemap` (tree-sitter TS).

## 4. Règles de code et conventions

- **Nommage** : **camelCase** (variables/fonctions), **PascalCase** (composants) ; `kebab-case` fichiers/slugs.
- **Typage & principes** : **strict** (`tsc` vert) ; réutiliser le design-system avant de créer du neuf ; état
  lisible d'un coup d'œil ; DRY.
- **Anti-patterns** (jamais) : **livrer un écran sans l'avoir vu** (boucle visuelle screenshot + Read) ;
  réinventer un primitive existant (`frontmap where` d'abord) ; signature d'API « de mémoire » avant un import
  non trivial → **si un MCP de corpus est câblé** (`cockpit mcp wire`), interroge le **silo tech pertinent**
  (`query(type=tech, scope=<silo>)` — `react` · `vite` · `typescript` · `tailwind` · `zod` · `vitest` ·
  `react-query` · `react-router` · `react-hook-form` · `shadcn` · `radix-ui`) ; sinon lis la doc/le code —
  n'invente pas ; `grep` aveugle ; commit direct `main`/`dev` ; merge/push **sans GO humain**.

## 5. Format des réponses attendues

- **Code** : blocs **complets** pour un fichier neuf ; **extraits ciblés** pour une retouche.
- **Langue** : échanges en **français** ; code, identifiants et **commentaires en anglais**.
- **Concision** : va au résultat.

## 6. Workflows et processus

- **Boucle** : `roadmap-decompose` → `work-loop` (feature depuis `dev`, gate vert, ff-only, `main` promu depuis
  `dev`) → `docs-authoring`. GO humain fail-closed pour tout acte irréversible.
- **Séquence back→front** : une feature `frontend` qui consomme une API se travaille **après le merge** de la
  feature `backend` dans `dev` (la worktree en verra le contrat).
- **Tests** : **vitest** pour la logique ; la vérif d'écran passe par la **boucle visuelle** (screenshot +
  Read). Gate vert avant tout commit.
- **Documentation** : intention + design-system + contrats dans `docs/` ; après y avoir touché, `docsmap build
  && docsmap check`. Skills embarqués : `roadmap-decompose`, `docs-authoring`, `work-loop`, `quality-gate`.
