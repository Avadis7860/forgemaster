// prod.ts — entrypoint de PRODUCTION semé par le cockpit (deploy chain). Un seul process, un seul port :
// sert le front buildé (Vite → dist/) ET l'API Hono autoritative derrière 0.0.0.0:${PORT|8000}. Le cockpit
// publie ${COCKPIT_PORT} → 8000 via compose. Le dev reste bi-process (Vite + `dev:server` sur 8787, proxy
// /api) ; la PROD réunifie tout ici. Étends au fil des features en gardant ce contrat mono-port.
import { serve } from "@hono/node-server";
import { serveStatic } from "@hono/node-server/serve-static";
import { Hono } from "hono";

import { app as api } from "./index.js";

const app = new Hono();

// L'API autoritative d'abord (préséance sur le catch-all statique).
app.route("/", api);
// Les assets buildés (dist/ à la racine du projet = cwd /app du conteneur). serveStatic passe la main
// (next) sur un miss → le fallback SPA ci-dessous prend le relais pour les routes client.
app.use("/*", serveStatic({ root: "./dist" }));
app.get("*", serveStatic({ path: "./dist/index.html" }));

const port = Number(process.env.PORT ?? 8000);
serve({ fetch: app.fetch, port, hostname: "0.0.0.0" });
