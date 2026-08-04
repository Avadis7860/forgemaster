// index.ts — point d'entrée TS d'amorçage semé par le forgemaster. Donne à `tsc --noEmit` (le gate Tier-0) une
// entrée à vérifier dès la création. Remplace-le par ta vraie app (React/Vite sous `web/`, logique sous
// `src/`) au fil des features ; garde le contrat de types strict (tsc vert au gate).
export const seed = "forgemaster-front-seed" as const;
