// index.ts — serveur autoritatif d'amorçage (Hono). Décision verrouillée 2 : l'état canonique et la résolution
// vivent ICI ; le client (`web/`) propose, le serveur dispose. Amorçage : une route de santé qui valide le
// modèle PARTAGÉ (`src/shared`). Étends-le en É2 (boucle de tick) / É3 (commandes + API validées Zod).
import { serve } from "@hono/node-server";
import { Hono } from "hono";

import { Player } from "../src/shared/schema.js";

export const app = new Hono();

app.get("/api/health", (c) => {
  const seed = Player.parse({
    id: "00000000-0000-0000-0000-000000000000",
    name: "server",
    resources: [{ kind: "credits", amount: 0 }],
  });
  return c.json({ ok: true, player: seed });
});

// Lancé directement (`npm run dev:server`) → écoute ; importé (tests) → n'ouvre aucun port.
if (import.meta.url === `file://${process.argv[1]}`) {
  serve({ fetch: app.fetch, port: 8787 });
}
