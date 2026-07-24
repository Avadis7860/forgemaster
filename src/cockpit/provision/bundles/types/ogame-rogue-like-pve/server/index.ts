// index.ts — serveur AUTORITATIF d'amorçage (Hono). Décision verrouillée 2 : l'état canonique et sa
// résolution vivent ICI ; le client (`web/`) propose, le serveur dispose. Amorçage NÉ-AVEC : une boucle de
// tick déterministe (`src/shared/tick`) sur un état canonique + un canal WebSocket d'écho autoritatif qui
// pousse l'état après chaque tick. Le client CONSOMME cet état (il ne le calcule jamais). Point d'extension :
// étends les réducteurs (`schema`/`tick`) et, en É3, accepte les commandes entrantes sur le WS (validées Zod).
import { serve, type ServerType } from "@hono/node-server";
import { createNodeWebSocket } from "@hono/node-ws";
import { Hono } from "hono";
import type { WSContext } from "hono/ws";

import { Player, type GameState } from "../src/shared/schema.js";
import { applyTick } from "../src/shared/tick.js";

export const app = new Hono();

const TICK_SEED = 1; // seed de simulation (amorçage — dérive-le de la partie/monde quand tu auras des sessions)
const TICK_MS = 1000; // cadence d'amorçage

// État canonique du serveur (autoritatif). Sa mutation est réservée à la boucle de tick + aux commandes
// validées serveur — jamais au client.
let state: GameState = { tick: 0, resources: [{ kind: "credits", amount: 0 }] };

app.get("/api/health", (c) => {
  const player = Player.parse({
    id: "00000000-0000-0000-0000-000000000000",
    name: "server",
    resources: [{ kind: "credits", amount: 0 }],
  });
  return c.json({ ok: true, tick: state.tick, player });
});

// attachGameLoop — câble le canal WS d'écho autoritatif sur `target` et rend un démarreur (appelé une fois le
// serveur créé). Partagé par les deux entrypoints (DEV `index` / PROD `prod`) → une seule vérité pour la
// boucle née-avec. À l'ouverture et à chaque tick, l'état canonique est POUSSÉ à tous les clients connectés.
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
      state = applyTick(state, TICK_SEED);
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
