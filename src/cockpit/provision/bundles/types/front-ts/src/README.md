# src/ — source TypeScript de l'app (front-ts)

Le code applicatif TypeScript. Le cadre de stack et les conventions vivent dans le `CLAUDE.md` racine et
`docs/architecture.md` — lis-les avant de structurer.

## Contenu

- **`index.ts`** — l'entrée. Point de départ du squelette semé, à étoffer au fil du travail.
- L'arbre source (composants, modules, types) s'organise ici ; le service de run (Dockerfile/compose,
  `server.mjs`) vit à la racine.

## Règle

Typage strict (`tsc`), cœur testable sans I/O, un test par capacité livrée. Le gate (lint → types → tests)
doit rester vert avant tout commit. Interroge `codemap` avant de `grep`/lire en bloc.
