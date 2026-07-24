// index.ts — serveur AUTORITATIF (Hono). Décision verrouillée 2 : l'état canonique et sa résolution vivent ICI ;
// le client (`web/`) propose, le serveur dispose. Le moteur né-avec (`src/shared/tick`) tourne sur l'état
// canonique et est POUSSÉ via un canal WebSocket d'écho autoritatif après chaque tick. Le client CONSOMME cet
// état (il ne le calcule jamais). Point d'extension F4 (2e) : accepter les commandes ENTRANTES sur le WS
// (validées Zod par `applyCommand`) — aujourd'hui le canal est push-only.
import { serve, type ServerType } from "@hono/node-server";
import { createNodeWebSocket } from "@hono/node-ws";
import { Hono } from "hono";
import type { WSContext } from "hono/ws";

import type { GameState } from "../src/shared/schema.js";
import { applyTick, initialGameState } from "../src/shared/tick.js";

export const app = new Hono();

const TICK_MS = 1000; // cadence d'amorçage (le worker règle universeSpeed/cadence pour un run de 30–90 min)

// État canonique du serveur (autoritatif). Sa mutation est réservée à la boucle de tick + aux commandes
// validées serveur — jamais au client.
let state: GameState = initialGameState({ runSeed: 1 });

app.get("/api/health", (c) => c.json({ ok: true, tick: state.tick, resources: state.resources }));

// attachGameLoop — câble le canal WS d'écho autoritatif sur `target` et rend un démarreur (appelé une fois le
// serveur créé). Partagé DEV (`index`) / PROD (`prod`). À l'ouverture et à chaque tick, l'état canonique est
// POUSSÉ à tous les clients connectés.
export function attachGameLoop(target: Hono): (server: ServerType) => void {
  const { injectWebSocket, upgradeWebSocket } = createNodeWebSocket({ app: target });
  const clients = new Set<WSContext>();
  target.get(
    "/ws",
    upgradeWebSocket(() => ({
      onOpen: (_evt, ws) => {
        clients.add(ws);
        ws.send(JSON.stringify(state));
      },
      onClose: (_evt, ws) => {
        clients.delete(ws);
      },
    })),
  );
  return (server) => {
    injectWebSocket(server);
    setInterval(() => {
      state = applyTick(state);
      const payload = JSON.stringify(state);
      for (const ws of clients) ws.send(payload);
    }, TICK_MS);
  };
}

// Lancé directement (`npm run dev:server`) → écoute + tick + WS ; importé (tests/prod) → aucun port, aucun
// timer (la PROD pilote la boucle via `attachGameLoop`, cf. `prod.ts`).
if (import.meta.url === `file://${process.argv[1]}`) {
  const startGameLoop = attachGameLoop(app);
  const server = serve({ fetch: app.fetch, port: 8787 });
  startGameLoop(server);
}
