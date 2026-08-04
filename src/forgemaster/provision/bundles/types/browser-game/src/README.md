# src/ — univers TypeScript unifié (browser-game)

Le code du jeu, dans un **univers TS unifié** (même langage client/serveur). Le cadre de stack est
**VERROUILLÉ** dans le `CLAUDE.md` racine (serveur autoritatif, déterminisme) — ne le re-débats pas.

## Contenu

- **`shared/`** — le **modèle de domaine partagé** (schémas Zod). Source unique de vérité des types qui
  traversent client et serveur ; commence toute évolution du domaine ici.
- **`index.ts`** — l'entrée. Étoffe le squelette semé sans casser le contrat partagé.
- `web/` (client Vite/React) et `server/` (Hono) s'ajoutent au fil du travail selon le cadre verrouillé.

## Règle

Le domaine se modélise **d'abord** dans `shared/` (Zod), puis se consomme des deux côtés — jamais un type
dupliqué client vs serveur. Le gate (`tsc` → tests) doit rester vert. Interroge `codemap` avant de fouiller.
