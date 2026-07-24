# Template — manifest npm + contrat de gate semés à l'amorçage (racine de l'univers TS-mono)

> Émis par l'**É0** du blueprint `browser-game-pve` (décision verrouillée **1** — un seul univers TypeScript,
> `web/` + `server/` + Zod partagés — et **2**/**5** — serveur-autoritatif, déterminisme). Depuis le découplage
> moteur↔derive (**P2**, cf. `docs/specs/ogame-rogue-like-pve-bundle.md`), ce template ne dérive plus QUE
> l'**épine structurelle** : le **`package.json`** (stack verrouillée + script `gate` `eslint → tsc → vitest`,
> jetons d'archétype remplis au build) — et, hors scaffold, la région **§6** du `CLAUDE.md` (patron d'étapes
> splicé depuis `blueprint.md`, le vrai levier de compounding). Le **moteur de jeu** (`src/shared/`, `server/`,
> `web/`, configs TS) est désormais **hand-authored directement dans l'overlay** du bundle : c'est du **contenu
> de bundle** (le jeu ogame crash-test fini né-avec), pas du capital central — un moteur quasi-complet ne rentre
> pas dans des fences markdown, et ce qui gradue au centre est le **blueprint**, jamais la source du moteur.
> `npm install && npm run gate` (`eslint . && tsc --noEmit && vitest run`) reste **vert sans édition**. Remplace
> le jeton de mission `{{theme}}` au fil des features (les jetons `{{game_name}}` vivent dans les fichiers moteur
> de l'overlay) ; ne re-débats PAS la stack.

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
    "@tanstack/react-query": "^5.62.0",
    "hono": "^4.6.14",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "zod": "{{zod_version}}"
  },
  "devDependencies": {
    "@eslint/js": "^9.17.0",
    "@tailwindcss/vite": "^4.0.0",
    "@types/node": "^22.10.2",
    "@types/react": "^19.0.2",
    "@types/react-dom": "^19.0.2",
    "@vitejs/plugin-react": "^4.3.4",
    "eslint": "^9.17.0",
    "eslint-plugin-react-hooks": "^5.1.0",
    "tailwindcss": "^4.0.0",
    "tsx": "^4.19.2",
    "typescript": "{{ts_version}}",
    "typescript-eslint": "^8.18.0",
    "vite": "^5.4.11",
    "vitest": "^2.1.8"
  }
}
```

## Ce que tu NE dois pas re-débattre (hérité du blueprint)

- **Un seul univers TypeScript (décision 1)** : `web/` (Vite/React) + `server/` (Hono) + Zod **partagés**. Pas de
  second langage back par défaut — le modèle de domaine vit dans `src/shared/`, importé des deux côtés.
- **Le squelette est né-avec, pas scaffoldé par un worker (É0)** : un projet frais est runnable (client servi par
  Vite, gate `eslint → tsc → vitest` vert) SANS édition manuelle ; la toolchain verrouillée est **née-avec**
  (eslint flat-config, Tailwind v4, React Query) — un worker task-scopé n'a jamais à poser
  `package.json`/`tsconfig`/config lint-style/`web/`/`server/`.
- **Serveur-autoritatif, déterministe (décisions 2 et 5)** : l'état canonique et la résolution vivent côté serveur ;
  le client propose, le serveur dispose. Pas de logique de jeu côté client. La boucle de tick est amorcée
  né-avec (`src/shared/tick` exécuté par `server/index.ts`, poussé sur `GET /ws`) — le moteur (hand-authored
  dans l'overlay) l'étend, il ne la refonde pas.
- **Persistance SQLite + Drizzle (décision 3)** : ne monte pas Postgres ni un ORM lourd avant que l'échelle
  l'exige. Câblée avec la structure roguelike du moteur (crash-test), pas au niveau de ce manifest.
- **Jetons de mission (`{{game_name}}`, `{{theme}}`) restent à remplir** : ils sont propres au projet — le
  game-design (économie, factions, map) vit dans le `CLAUDE.md` du projet et le moteur de l'overlay, pas ici.
```
