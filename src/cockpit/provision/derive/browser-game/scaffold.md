# Template — squelette TS-mono runnable semé à l'amorçage : univers TypeScript unifié, Zod partagé, gate vert

> Émis par l'**É0** du blueprint `browser-game-pve` (décisions verrouillées **1** — un seul univers TypeScript,
> front + back + Zod partagés — et **2**/**5** — serveur-autoritatif, déterminisme). À la création d'un projet de la
> classe, semer dans son SoT un **squelette runnable out-of-the-box** : `package.json` + `tsconfig.json` + un modèle
> de domaine **Zod partagé** + une entrée serveur — gate `tsc --noEmit` **vert sans édition**. Remplace les
> `{{jetons}}` de mission (`{{game_name}}`, `{{theme}}`) au fil des features ; ne re-débats PAS la stack.

### `package.json` (semé à la racine du SoT)

```json
{
  "name": "{{pkg_name}}",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "description": "Jeu navigateur (univers TypeScript unifié) semé par le cockpit — thème : {{theme}}. Le script `gate` monte la toolchain Tier-0.",
  "scripts": {
    "gate": "{{gate_cmd}}"
  },
  "dependencies": {
    "zod": "{{zod_version}}"
  },
  "devDependencies": {
    "typescript": "{{ts_version}}"
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

### `src/index.ts` (semé — entrée serveur-autoritative d'amorçage)

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

## Ce que tu NE dois pas re-débattre (hérité du blueprint)

- **Un seul univers TypeScript (décision 1)** : `web/` + `server/` + Zod **partagés**. Pas de second langage back
  par défaut — le modèle de domaine vit dans `src/shared/`, importé des deux côtés.
- **Le squelette est né-avec, pas scaffoldé par un worker (É0)** : un projet frais est runnable (gate vert) SANS
  édition manuelle ; un worker task-scopé n'a jamais à poser `package.json`/`tsconfig`.
- **Serveur-autoritatif, déterministe (décisions 2 et 5)** : l'état canonique et la résolution vivent côté serveur ;
  le client propose, le serveur dispose. Pas de logique de jeu côté client.
- **Persistance SQLite + Drizzle au départ (décision 3)** : ne monte pas Postgres ni un ORM lourd avant que
  l'échelle l'exige.
- **Jetons de mission (`{{game_name}}`, `{{theme}}`) restent à remplir** : ils sont propres au projet — le
  game-design (économie, factions, map) vit dans le `CLAUDE.md` du projet, pas dans ce squelette.
```
