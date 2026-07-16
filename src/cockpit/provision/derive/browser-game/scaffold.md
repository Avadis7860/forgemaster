# Template — squelette TS-mono runnable semé à l'amorçage : univers TypeScript unifié, web + server + Zod partagé, gate vert

> Émis par l'**É0** du blueprint `browser-game-pve` (décisions verrouillées **1** — un seul univers TypeScript,
> `web/` + `server/` + Zod partagés — et **2**/**5** — serveur-autoritatif, déterminisme). À la création d'un projet
> de la classe, semer dans son SoT un **squelette runnable out-of-the-box** : `package.json` + `tsconfig.json` + un
> modèle de domaine **Zod partagé** (+ son test), un **client** Vite/React (`web/`) et un **serveur** Hono (`server/`)
> — `npm install && npm run dev` sert le client, `npm run gate` (`tsc --noEmit && vitest run`) est **vert sans
> édition**. Remplace les `{{jetons}}` de mission (`{{game_name}}`, `{{theme}}`) au fil des features ; ne re-débats
> PAS la stack.

### `package.json` (semé à la racine du SoT)

```json
{
  "name": "{{pkg_name}}",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "description": "Jeu navigateur (univers TypeScript unifié) semé par le cockpit — thème : {{theme}}. Le script `gate` monte la toolchain Tier-0.",
  "scripts": {
    "dev": "vite",
    "dev:server": "tsx watch server/index.ts",
    "build": "vite build",
    "test": "vitest run",
    "gate": "{{gate_cmd}}"
  },
  "dependencies": {
    "@hono/node-server": "^1.13.7",
    "hono": "^4.6.14",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "zod": "{{zod_version}}"
  },
  "devDependencies": {
    "@types/node": "^22.10.2",
    "@types/react": "^19.0.2",
    "@types/react-dom": "^19.0.2",
    "@vitejs/plugin-react": "^4.3.4",
    "tsx": "^4.19.2",
    "typescript": "{{ts_version}}",
    "vite": "^5.4.11",
    "vitest": "^2.1.8"
  }
}
```

### `tsconfig.json` (semé)

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src", "web", "server"]
}
```

### `src/shared/schema.ts` (semé — modèle de domaine Zod partagé, source unique de vérité du type universe)

```typescript
// schema.ts — modèle de domaine PARTAGÉ (décision verrouillée 1 : un seul univers TS, schémas Zod partagés
// entre `web/` et `server/`). Source unique de vérité du type universe. Amorçage minimal : ressources + joueur ;
// étends-le en É1 (unités, bâtiments, map, bots) — le serveur reste l'autorité (décision 2).
import { z } from "zod";

export const Resource = z.object({
  kind: z.enum(["credits", "matter", "energy"]),
  amount: z.number().int().nonnegative(),
});
export type Resource = z.infer<typeof Resource>;

export const Player = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  resources: z.array(Resource),
});
export type Player = z.infer<typeof Player>;
```

### `src/shared/schema.test.ts` (semé — test PUR du modèle partagé, Vitest)

```typescript
// schema.test.ts — test PUR du modèle de domaine partagé (Vitest, aucune I/O : la résolution se teste en pur
// avant l'UI, décision verrouillée). Il prouve que le gate `tsc --noEmit && vitest run` est vert dès l'amorçage
// et fixe l'invariant « une capacité livrée = un test ». Étends-le au fil du modèle (unités, combat, tick).
import { describe, expect, it } from "vitest";

import { Player, Resource } from "./schema.js";

describe("modèle de domaine partagé", () => {
  it("accepte un joueur d'amorçage valide", () => {
    const player = Player.parse({
      id: "00000000-0000-0000-0000-000000000000",
      name: "seed",
      resources: [{ kind: "credits", amount: 0 }],
    });
    expect(player.resources[0]?.kind).toBe("credits");
  });

  it("rejette une ressource au montant négatif (invariant serveur-autoritatif)", () => {
    expect(() => Resource.parse({ kind: "energy", amount: -1 })).toThrow();
  });
});
```

### `src/index.ts` (semé — entrée d'amorçage, valide le modèle partagé)

```typescript
// index.ts — entrée TS d'amorçage semée par le cockpit (univers unifié : client `web/` + serveur autoritatif
// `server/`). Valide le modèle partagé pour que `tsc --noEmit` (gate Tier-0) ait une entrée à vérifier dès la
// création. Remplace-la par ta vraie boucle de tick déterministe (décisions 2 et 5) au fil des features.
import { Player } from "./shared/schema.js";

export const seed: Player = {
  id: "00000000-0000-0000-0000-000000000000",
  name: "{{game_name}}",
  resources: [{ kind: "credits", amount: 0 }],
};
```

### `server/index.ts` (semé — serveur autoritatif d'amorçage, Hono)

```typescript
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
```

### `web/index.html` (semé — entrée du client Vite)

```html
<!doctype html>
<html lang="fr">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{game_name}}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/main.tsx"></script>
  </body>
</html>
```

### `web/main.tsx` (semé — point de montage du client React)

```typescript
// main.tsx — point de montage du client React (Vite). Le client est une VUE + des commandes : aucune logique
// de jeu ici (anti-triche, décision verrouillée 2). Étends l'UI en É6 (panneaux ressources/map/flotte).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App.js";

const root = document.getElementById("root");
if (!root) throw new Error("élément #root introuvable dans index.html");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

### `web/App.tsx` (semé — composant racine d'amorçage)

```typescript
// App.tsx — composant racine d'amorçage. Consomme le modèle de domaine PARTAGÉ (`src/shared/schema`) pour
// prouver l'univers TypeScript unifié (décision verrouillée 1). Remplace-le par ta vraie UI de gestion (É6).
import type { Player } from "../src/shared/schema.js";

// Jetons de mission (remplis par le worker du projet) — gardés en littéraux de chaîne pour que le squelette
// reste tsc-vert AVANT leur remplacement.
const gameName = "{{game_name}}";
const theme = "{{theme}}";

const seed: Player = {
  id: "00000000-0000-0000-0000-000000000000",
  name: gameName,
  resources: [{ kind: "credits", amount: 0 }],
};

export function App() {
  return (
    <main>
      <h1>{gameName}</h1>
      <p>Squelette semé — thème : {theme}. Le serveur est l'autorité ; ce client n'est qu'une vue.</p>
      <ul>
        {seed.resources.map((r) => (
          <li key={r.kind}>
            {r.kind} : {r.amount}
          </li>
        ))}
      </ul>
    </main>
  );
}
```

### `vite.config.ts` (semé — build/dev du client `web/`)

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite : racine = web/ (client React). Le dev-server proxifie /api vers le serveur Hono (port 8787) ; le build
// sort dans dist/. Univers TypeScript unifié : web/ et server/ partagent src/shared (hors racine web/).
export default defineConfig({
  root: "web",
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8787",
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
});
```

### `vitest.config.ts` (semé — tests unitaires purs)

```typescript
import { defineConfig } from "vitest/config";

// Vitest : tests unitaires PURS (Node, aucun DOM au départ — la résolution se teste sans UI). Bascule
// `environment` à "jsdom" quand tu testeras des composants React (É6).
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
```

## Ce que tu NE dois pas re-débattre (hérité du blueprint)

- **Un seul univers TypeScript (décision 1)** : `web/` (Vite/React) + `server/` (Hono) + Zod **partagés**. Pas de
  second langage back par défaut — le modèle de domaine vit dans `src/shared/`, importé des deux côtés.
- **Le squelette est né-avec, pas scaffoldé par un worker (É0)** : un projet frais est runnable (client servi par
  Vite, gate `tsc → vitest` vert) SANS édition manuelle ; un worker task-scopé n'a jamais à poser
  `package.json`/`tsconfig`/`web/`/`server/`.
- **Serveur-autoritatif, déterministe (décisions 2 et 5)** : l'état canonique et la résolution vivent côté serveur ;
  le client propose, le serveur dispose. Pas de logique de jeu côté client.
- **Persistance SQLite + Drizzle au départ (décision 3)** : ne monte pas Postgres ni un ORM lourd avant que
  l'échelle l'exige. (Non encore câblée dans ce squelette d'amorçage — arrive en É2/É7.)
- **Jetons de mission (`{{game_name}}`, `{{theme}}`) restent à remplir** : ils sont propres au projet — le
  game-design (économie, factions, map) vit dans le `CLAUDE.md` du projet, pas dans ce squelette.
```
