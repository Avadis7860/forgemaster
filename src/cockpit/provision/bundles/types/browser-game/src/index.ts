// index.ts — point d'entrée TS d'amorçage semé par le cockpit (univers TypeScript unifié : client `web/` +
// serveur autoritatif `server/`). Donne à `tsc --noEmit` (le gate Tier-0) une entrée à vérifier dès la
// création. Remplace-le par ta vraie boucle de jeu (ticks déterministes, serveur autoritatif) au fil des features.
export const seed = "cockpit-game-seed" as const;
