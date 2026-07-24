# Architecture — browser-game

> Point de départ de la doc **technique** de ce projet : un **jeu navigateur** générique (genre défini par le
> projet), univers **TypeScript unifié**, indexé par `codemap` (engine ts, sous-systèmes `web`/`server`).
> Distincte de `docs/design.md` (le **quoi** du jeu) : ici le **comment technique**. Étoffe cette page au fil
> du travail — `docsmap where "<intention>"` la rend interrogeable.

## Intention
_(À renseigner.)_ Ce que produit le MVP jouable (ex. la boucle cœur de ton jeu, état serveur-autoritatif
persistant) et son critère binaire de succès. Le **design du jeu** (règles, contenu, progression) vit dans
`docs/design.md`, pas ici.

## Stack (verrouillée)
Univers **TypeScript unique** — cf. `CLAUDE.md §3` :
- **Front** `web/` : React 19 + Vite + TypeScript + Tailwind (CSR, UI web).
- **Back** `server/` : Hono (état serveur-autoritatif ; **persistance Drizzle + SQLite** câblée plus tard, en mémoire à l'amorçage).
- **Liant** : schémas **Zod** partagés client/serveur. **Tests** : Vitest. **Temps réel** : React Query
  (poll) + WebSocket Hono (events critiques).

## Où vit quoi
- `web/` — le front (composants, écrans, appels API). Interroge-le via `codemap where` / `frontmap where`.
- `server/` — la simulation serveur-autoritative (tick, commandes, résolution, persistance plus tard).
- `docs/design.md` — le foyer de la conception (game-design) ; `docs/` — la prose durable, via `docsmap`.
- `tests/` — un test par capacité ; la résolution de sim se teste **en pur** avant l'UI.

## Comment ce projet se travaille
Facette par défaut **backend** (serveur-autoritatif) : la logique de jeu est une fonction pure
`(état, commandes, seed) → état'`, testée sans I/O ; doc-first (interroge le **silo tech pertinent** —
`react`/`hono`/`zod`/`vitest`… — avant un import non trivial) ; gate `eslint`+`tsc`+`vitest` vert avant tout
commit. Le **design** (facette game-design, NO-CODE) se décide dans `docs/design.md` ; le **front** (facette
frontend) affiche l'état serveur, jamais de règle côté client. Boucle `work-loop` (feature depuis `dev`, gate
vert, `main` promu depuis `dev`).

## Contrats
- **Serveur-autoritatif** : le client propose une commande, le serveur la valide (schéma Zod) et l'applique.
  Aucune logique de jeu côté client (anti-triche).
- **Déterminisme** : même seed + même suite de commandes → même état ; la résolution se teste en pur.
- **Primitive née-avec** : le contrat partagé porte `GameState` + `Command` (`src/shared/schema.ts`) ; le cœur
  déterministe `(état, commandes, seed) → état'` vit en `src/shared/tick.ts` (`applyTick`/`applyCommand`, testés
  `src/shared/tick.test.ts`) ; le serveur exécute la boucle de tick sur l'état canonique et pousse l'état via un
  **canal WebSocket d'écho autoritatif** `GET /ws` (`server/index.ts`, `attachGameLoop` ; réuni en prod
  mono-port par `server/prod.ts`). **Remplace** le modèle placeholder par celui de ton jeu et **étends** cette
  primitive — ne la refonde pas.
