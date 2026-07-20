# Architecture — browser-game

> Point de départ de la doc **technique** de ce projet : un **jeu navigateur de gestion PvE vs bots**
> (OGame-like), univers **TypeScript unifié**, indexé par `codemap` (engine ts, sous-systèmes `web`/`server`).
> Distincte de `docs/design.md` (le **quoi** du jeu) : ici le **comment technique**. Étoffe cette page au fil
> du travail — `docsmap where "<intention>"` la rend interrogeable.

## Intention
_(À renseigner.)_ Ce que produit le MVP jouable (ex. boucle ressource→construction→flotte→combat contre des
bots, état serveur-autoritatif persistant) et son critère binaire de succès. Le **design du jeu** (économie,
boucles, factions, map) vit dans `docs/design.md`, pas ici.

## Stack (verrouillée)
Univers **TypeScript unique** — cf. `CLAUDE.md §3` :
- **Front** `web/` : React 19 + Vite + TypeScript + Tailwind (CSR, UI de gestion).
- **Back** `server/` : Hono + Drizzle + SQLite (état serveur-autoritatif).
- **Liant** : schémas **Zod** partagés client/serveur. **Tests** : Vitest. **Temps réel** : React Query
  (poll) + WebSocket Hono (events critiques).

## Où vit quoi
- `web/` — le front (composants, écrans, appels API). Interroge-le via `codemap where` / `frontmap where`.
- `server/` — la simulation serveur-autoritative (tick, commandes, combat, persistance Drizzle).
- `docs/design.md` — le foyer de la conception (game-design) ; `docs/` — la prose durable, via `docsmap`.
- `tests/` — un test par capacité ; la résolution de sim se teste **en pur** avant l'UI.

## Comment ce projet se travaille
Facette par défaut **backend** (serveur-autoritatif) : la logique de jeu est une fonction pure
`(état, commandes, seed) → état'`, testée sans I/O ; doc-first (interroge le MCP `browser-game` avant un
import non trivial React Query/Hono/Drizzle/Zod) ; gate `eslint`+`tsc`+`vitest` vert avant tout commit.
Le **design** (facette game-design, NO-CODE) se décide dans `docs/design.md` ; le **front** (facette frontend)
affiche l'état serveur, jamais de règle côté client. Boucle `work-loop` (feature depuis `dev`, gate vert,
`main` promu depuis `dev`).

## Contrats
- **Serveur-autoritatif** : le client propose une commande, le serveur la valide (schéma Zod) et l'applique.
  Aucune logique de jeu côté client (anti-triche).
- **Déterminisme** : même seed + même suite de commandes → même état ; la résolution se teste en pur.
- **Patron d'étapes** (blueprint browser-game-pve) : modèle Zod → tick serveur → commandes → IA bots →
  combat → UI → persistance. _(chaque étape se documente ici quand elle est franchie)_
  - **É1 (modèle) + É2 (tick serveur) — AMORCÉS né-avec.** Le contrat partagé porte `GameState` + `Command`
    (`src/shared/schema.ts`) ; le cœur déterministe `(état, commandes, seed) → état'` vit en
    `src/shared/tick.ts` (`applyTick`/`applyCommand`, testés `src/shared/tick.test.ts`) ; le serveur exécute
    la boucle de tick sur l'état canonique et pousse l'état via un **canal WebSocket d'écho autoritatif**
    `GET /ws` (`server/index.ts`, `attachGameLoop` ; réuni en prod mono-port par `server/prod.ts`). **Étends**
    cette primitive (production réelle, commandes entrantes en É3, combat en É5) — ne la refonde pas.
