// index.ts — entrée TS d'amorçage semée par le cockpit (univers unifié : client `web/` + serveur autoritatif
// `server/`). Valide le modèle partagé pour que `tsc --noEmit` (gate Tier-0) ait une entrée à vérifier dès la
// création. Remplace-la par ta vraie boucle de tick déterministe (décisions 2 et 5) au fil des features.
import { Player } from "./shared/schema.js";

export const seed: Player = {
  id: "00000000-0000-0000-0000-000000000000",
  name: "{{game_name}}",
  resources: [{ kind: "credits", amount: 0 }],
};
