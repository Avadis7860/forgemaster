# Template — squelette TS-mono runnable semé à l'amorçage : univers TypeScript unifié, web + server + Zod partagé, gate vert

> Émis par l'**É0** du blueprint `browser-game-pve` (décisions verrouillées **1** — un seul univers TypeScript,
> `web/` + `server/` + Zod partagés — et **2**/**5** — serveur-autoritatif, déterminisme). À la création d'un projet
> de la classe, semer dans son SoT un **squelette runnable out-of-the-box** : `package.json` + `tsconfig.json` + un
> modèle de domaine **Zod partagé** (+ son test), un **cœur de tick DÉTERMINISTE partagé** (`src/shared/tick`,
> `(état, commandes, seed) → état'`, testé), un **client** Vite/React (`web/`) et un **serveur** Hono (`server/`)
> qui fait tourner la boucle de tick sur l'état canonique et la **pousse via un canal WebSocket d'écho
> autoritatif** (`GET /ws`) — `npm install && npm run dev` sert le client, `npm run gate` (`tsc --noEmit &&
> vitest run`) est **vert sans édition**. Le worker **ÉTEND** cette boucle née-avec (production réelle, combat,
> bots), il ne la fonde pas. Remplace les `{{jetons}}` de mission (`{{game_name}}`, `{{theme}}`) au fil des
> features ; ne re-débats PAS la stack.

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
    "@hono/node-ws": "^1.3.1",
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

export const ResourceKind = z.enum(["credits", "matter", "energy"]);
export type ResourceKind = z.infer<typeof ResourceKind>;

export const Resource = z.object({
  kind: ResourceKind,
  amount: z.number().int().nonnegative(),
});
export type Resource = z.infer<typeof Resource>;

export const Player = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  resources: z.array(Resource),
});
export type Player = z.infer<typeof Player>;

// GameState — état canonique du jeu, avancé UNIQUEMENT par le serveur autoritatif (décision 2). Amorçage
// minimal : un compteur de tick + les ressources. Étends-le (unités, bâtiments, map, bots) en gardant le
// serveur seul maître de sa mutation.
export const GameState = z.object({
  tick: z.number().int().nonnegative(),
  resources: z.array(Resource),
});
export type GameState = z.infer<typeof GameState>;

// Command — geste joueur PROPOSÉ par le client, VALIDÉ + appliqué par le serveur (anti-triche : le client
// propose, le serveur dispose). Union discriminée à une variante d'amorçage (dépenser une ressource) —
// étends-la (`build`, `move`, `attack`…) sans jamais déplacer la validation côté client.
export const Command = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("spend"),
    resource: ResourceKind,
    amount: z.number().int().positive(),
  }),
]);
export type Command = z.infer<typeof Command>;
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

### `src/shared/tick.ts` (semé — cœur DÉTERMINISTE partagé `(état, commandes, seed) → état'`)

```typescript
// tick.ts — cœur DÉTERMINISTE partagé de la simulation : `(état, commandes, seed) → état'` (contrat
// verrouillé). Fonctions PURES, sans I/O ni UI (décision verrouillée : la résolution se teste avant l'écran).
// Univers TS unique (décision 1) : le type vit en `shared`. MAIS l'AUTORITÉ reste serveur (décision 2) —
// SEUL le serveur exécute ces réducteurs sur l'état canonique ; le client lit l'état poussé, il ne dérive
// JAMAIS l'état canonique lui-même (anti-triche). Point d'extension : ÉTENDS `applyTick`/`applyCommand`
// (production par bâtiment, combat, IA bots…), ne les refonde pas.
import { Command, type GameState } from "./schema.js";

// mulberry32 — PRNG déterministe minimal. Le `seed` compte réellement → le déterminisme est PROUVABLE
// (rejeu identique), pas tautologique. Remplace/étends par ta vraie source d'aléa seedée (événements, loot).
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// hashSeed — mélange `(seed, tick)` en une graine DÉCORRÉLÉE : deux ticks consécutifs (ou deux seeds
// proches) ne produisent pas de séquences corrélées (un simple `seed + tick` le ferait). Étends vers un vrai
// flux d'aléa (état RNG porté par `GameState`) le jour où tu auras des événements/loot seedés.
function hashSeed(seed: number, tick: number): number {
  let h = Math.imul(seed ^ 0x9e3779b9, 0x85ebca6b);
  h = Math.imul(h ^ tick ^ (h >>> 13), 0xc2b2ae35);
  return (h ^ (h >>> 16)) >>> 0;
}

const BASE_PRODUCTION = 10; // crédits produits par tick à l'amorçage — étends en taux par ressource/bâtiment.

// applyTick — avance d'un tick : incrémente le compteur et produit des ressources de façon déterministe
// (jitter seedé reproductible pour un couple `(seed, tick)` donné). Pur : ne mute pas `state`.
export function applyTick(state: GameState, seed: number): GameState {
  const rng = mulberry32(hashSeed(seed, state.tick)); // séquence reproductible, décorrélée par tick
  const resources = state.resources.map((r) => {
    if (r.kind !== "credits") return r;
    const jitter = Math.floor(rng() * 5); // 0..4, déterministe pour (seed, tick)
    return { ...r, amount: r.amount + BASE_PRODUCTION + jitter };
  });
  return { tick: state.tick + 1, resources };
}

// applyCommand — applique un geste joueur VALIDÉ. Commande mal formée (schéma) OU infaisable (solde
// insuffisant) → état INCHANGÉ, même référence (le serveur dispose). Réducteur total : ne lève jamais.
export function applyCommand(state: GameState, command: Command): GameState {
  const parsed = Command.safeParse(command);
  if (!parsed.success) return state;
  const cmd = parsed.data;
  if (cmd.kind === "spend") {
    const target = state.resources.find((r) => r.kind === cmd.resource);
    if (!target || target.amount < cmd.amount) return state; // solde insuffisant → refus (état identique)
    const resources = state.resources.map((r) =>
      r.kind === cmd.resource ? { ...r, amount: r.amount - cmd.amount } : r,
    );
    return { ...state, resources };
  }
  return state;
}
```

### `src/shared/tick.test.ts` (semé — test PUR du cœur de simulation, rend le déterminisme EXÉCUTABLE)

```typescript
// tick.test.ts — test PUR du cœur de simulation (Vitest, aucune I/O). Rend l'invariant verrouillé
// « même seed + même suite de commandes → même état » EXÉCUTABLE (pas juste déclaratif), et fixe l'invariant
// « une capacité livrée = un test ». Étends-le au fil du modèle (production par bâtiment, combat, IA bots).
import { describe, expect, it } from "vitest";

import { applyCommand, applyTick } from "./tick.js";
import type { GameState } from "./schema.js";

const seedState: GameState = { tick: 0, resources: [{ kind: "credits", amount: 0 }] };

describe("simulation déterministe (contrat verrouillé (état, commandes, seed) → état')", () => {
  it("même seed + même suite de commandes → même état (rejeu byte-identique)", () => {
    const run = (): GameState => {
      let s = seedState;
      for (let i = 0; i < 10; i++) s = applyTick(s, 42);
      return applyCommand(s, { kind: "spend", resource: "credits", amount: 25 });
    };
    expect(run()).toEqual(run());
  });

  it("un seed différent produit une trajectoire différente (le seed compte réellement)", () => {
    // Compare la TRAJECTOIRE complète (le montant à chaque tick), pas l'état final : deux trajectoires
    // 10-ticks identiques exigeraient un jitter égal à CHAQUE tick (~(1/5)^10), pas juste une somme égale.
    const trajectory = (seed: number): number[] => {
      let s = seedState;
      const amounts: number[] = [];
      for (let i = 0; i < 10; i++) {
        s = applyTick(s, seed);
        amounts.push(s.resources[0]?.amount ?? 0);
      }
      return amounts;
    };
    expect(trajectory(1)).not.toEqual(trajectory(999));
  });

  it("un tick avance le compteur et produit des ressources", () => {
    const s = applyTick(seedState, 7);
    expect(s.tick).toBe(1);
    expect(s.resources[0]?.amount).toBeGreaterThan(0);
  });

  it("une dépense valide débite le solde", () => {
    const s: GameState = { tick: 3, resources: [{ kind: "credits", amount: 50 }] };
    const after = applyCommand(s, { kind: "spend", resource: "credits", amount: 20 });
    expect(after.resources[0]?.amount).toBe(30);
  });

  it("une dépense au-delà du solde est refusée (état inchangé — le serveur dispose)", () => {
    const s: GameState = { tick: 3, resources: [{ kind: "credits", amount: 5 }] };
    const after = applyCommand(s, { kind: "spend", resource: "credits", amount: 999 });
    expect(after).toBe(s); // même référence : refus strict, aucune mutation
  });

  it("applyTick ne mute pas l'état d'entrée (pureté)", () => {
    const s: GameState = { tick: 0, resources: [{ kind: "credits", amount: 0 }] };
    applyTick(s, 3);
    expect(s).toEqual({ tick: 0, resources: [{ kind: "credits", amount: 0 }] });
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
  le client propose, le serveur dispose. Pas de logique de jeu côté client. **É2 (boucle de tick) est amorcée
  né-avec** (`src/shared/tick` exécuté par `server/index.ts`, poussé sur `GET /ws`) — étends-la, ne la refonde pas.
- **Persistance SQLite + Drizzle au départ (décision 3)** : ne monte pas Postgres ni un ORM lourd avant que
  l'échelle l'exige. (Non encore câblée dans ce squelette d'amorçage — arrive en É7 ; l'état de tick est en
  mémoire à l'amorçage.)
- **Jetons de mission (`{{game_name}}`, `{{theme}}`) restent à remplir** : ils sont propres au projet — le
  game-design (économie, factions, map) vit dans le `CLAUDE.md` du projet, pas dans ce squelette.
```
