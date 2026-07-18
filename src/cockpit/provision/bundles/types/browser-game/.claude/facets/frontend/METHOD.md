# Méthode — facette Frontend (browser-game)

1. **Boucle visuelle** — tout changement d'écran : screenshot **puis Read** de la capture AVANT de livrer.
   Ambigu (« façon X ») → mockup A/B d'abord.
2. **Preuve de rendu (Tier-1.5)** — émets `.cockpit/verify-markers.json` = `{"markers":[…]}` : les chaînes FR
   **littérales** que ton écran rend (titres, labels de ton `acceptance`). La gate cherchera ces marqueurs dans
   le DOM du preview-deploy de ta feature — déclare le vrai, pas un vœu (un marqueur non rendu ⇒ gate rouge).
3. **Design-system d'abord** — `frontmap where` (tokens / primitives / routes) avant de créer du neuf.
4. **Serveur-autoritatif** — l'UI lit l'état (React Query poll + WebSocket events), envoie des commandes
   validées par des schémas **Zod partagés** ; jamais de règle de jeu calculée côté client.
5. **Doc-first (anti-boucle)** — avant un import non trivial (React Query / Zod), interroge le MCP
   (`query(type=tech, scope=browser-game)`) — pas de signature inventée.
6. **Gate** — `eslint` → `tsc` → `vitest` vert. Corrige la cause.
7. **Fraîcheur** — front touché → `frontmap build` (+ `codemap build` pour la logique partagée).
