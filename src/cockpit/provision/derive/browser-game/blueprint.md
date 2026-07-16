---
id: browser-game-pve
date: 2026-06-13
status: active
stack: browser-game
related_decisions:
  - stack-choices/2026-06-13--browser-game
  - ephemeral-env-bundle-model
related_catalogs:
  - react
  - hono
  - drizzle
  - zod
templates: [scaffold]
tags: [blueprint, browser-game, pve, roguelike, tick, server-authoritative, ts]
---

# Blueprint — jeu navigateur de gestion, PvE vs bots (roguelike-like)

> Patron AVANT-COUP pour un projet de la classe « OGame-like / roguelike PvE vs bots » (ex. void-runner).
> Appliqué par le Claude de l'env au démarrage (CLAUDE.md §« blueprint d'abord ») : on **n'en re-débat pas**
> les décisions verrouillées, on déroule le patron d'étapes en remplissant la mission spécifique du projet.

## Décisions stratégiques VERROUILLÉES

1. **Un seul univers TypeScript** (front React/Vite + back Hono + schémas **Zod partagés**). Pas de second
   langage back par défaut (cf. `stack-choices/2026-06-13--browser-game`).
2. **Simulation serveur-autoritative, par ticks déterministes.** Le client n'est qu'une vue + des commandes ;
   l'état canonique et la résolution (combat, production) vivent côté serveur → fairness PvE/anti-triche.
3. **Persistance SQLite + Drizzle** au départ (migration → Postgres seulement quand l'échelle l'exige).
4. **Temps réel hybride** : React Query (poll) pour l'UI de gestion ; WebSocket (Hono) pour les events
   critiques (combat, attaque de bot). Pas de moteur de jeu lourd (Phaser/PixiJS) tant que pas de rendu animé.
5. **Bots = IA serveur** (comportements déterministes/seedés au départ ; pas de ML avant besoin prouvé).

## Patron d'étapes

- **É0 — Amorçage / premier launch** : le projet naît avec un **squelette TS-mono runnable out-of-the-box** — un
  seul univers TypeScript (`web/` + `server/` + Zod partagés, décision verrouillée 1), gate `tsc`/`vitest` **vert
  dès la création**, aucun `package.json` à écrire à la main. On ne re-débat pas la stack : on remplit les
  `{{jetons}}` de mission (thème, nom) et on déroule É1. *Défaut : le squelette est **né-avec**, jamais scaffoldé
  par un worker task-scopé.* **Indice : §template.scaffold.**
- **É1 — Modèle de domaine** : schémas Zod partagés (ressources, unités, bâtiments, map, joueur/bot). Source
  unique de vérité du type universe. *Défaut : commencer par le modèle, pas par l'UI.*
- **É2 — Boucle de tick serveur** : scheduler déterministe (production, file de build) ; état en base (Drizzle).
  *Défaut : tick fixe côté serveur, jamais côté client.*
- **É3 — Commandes + API** : endpoints Hono (validés Zod) pour les actions joueur ; rejet serveur si illégal.
- **É4 — IA des bots** : comportements seedés/déterministes, intégrés à la boucle de tick.
- **É5 — Combat / résolution** : déterministe, serveur-autoritatif ; events poussés en WebSocket.
- **É6 — UI de gestion** : React + Tailwind (panneaux ressources/map/flotte/combat), React Query pour l'état.
- **É7 — Persistance & sessions** : auth joueur, sauvegarde d'état, reprise. *Défaut : SQLite → Postgres plus tard.*

## Défauts UX / technique

- **Pas de logique de jeu côté client** (anti-triche) — le client propose, le serveur dispose.
- **Déterminisme** : même seed + mêmes commandes → même état (rejouable, testable Vitest).
- **Tests** : la résolution (combat, production) se teste en pur (Vitest) avant toute UI.
- **Échelle différée** : SQLite/monolithe d'abord ; ne pas sur-architecturer (sharding, microservices) avant besoin.

## À remplir par projet (mission spécifique)

Le thème, l'économie précise, les races/factions, la map — propres à chaque jeu (void-runner = ex.) — vivent
dans le `CLAUDE.md` du projet, pas ici. Ce blueprint fixe le **cadre technique**, pas le game-design.
