# Méthode — facette Backend (browser-game, serveur-autoritatif)

1. **Résolution pure d'abord** — la logique de jeu (tick, économie, combat) est une fonction pure
   `(état, commandes, seed) → état'`, testée **sans I/O ni UI**. Déterminisme vérifié par test.
2. **Contrat explicite** — schémas **Zod partagés** client/serveur = source unique des types traversant la
   frontière. Une commande invalide est rejetée par le serveur (le client propose, le serveur dispose).
3. **Doc-first (anti-boucle)** — avant un import non trivial (Hono / Drizzle / Zod), interroge le MCP
   (`query(type=tech, scope=browser-game)`) — pas de signature inventée → pas de retry.
4. **Gate avant commit** — `eslint` → `tsc` → `vitest` vert. Corrige la cause, ne déplace pas un seuil.
5. **Fraîcheur carte** — code touché → `codemap build`.
