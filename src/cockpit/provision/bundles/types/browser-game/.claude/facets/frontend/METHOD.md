# Méthode — facette Frontend (browser-game)

1. **Boucle visuelle** — tout changement d'écran : screenshot **puis Read** de la capture AVANT de livrer.
   Ambigu (« façon X ») → mockup A/B d'abord.
2. **Design-system d'abord** — `frontmap where` (tokens / primitives / routes) avant de créer du neuf.
3. **Serveur-autoritatif** — l'UI lit l'état (React Query poll + WebSocket events), envoie des commandes
   validées par des schémas **Zod partagés** ; jamais de règle de jeu calculée côté client.
4. **Doc-first (anti-boucle)** — avant un import non trivial (React Query / Zod), interroge le MCP
   (`query(type=tech, scope=browser-game)`) — pas de signature inventée.
5. **Gate** — `eslint` → `tsc` → `vitest` vert. Corrige la cause.
6. **Fraîcheur** — front touché → `frontmap build` (+ `codemap build` pour la logique partagée).
