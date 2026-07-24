// index.ts — entrée TS d'amorçage (univers unifié : client `web/` + serveur autoritatif `server/`). Valide le
// modèle de domaine partagé contre son schéma Zod pour que `tsc --noEmit` (gate Tier-0) ait une entrée à
// vérifier dès la création. Le worker THÉMATISE / ÉQUILIBRE le moteur né-avec (il ne le fonde pas).
import { GameState } from "./shared/schema.js";
import { initialGameState } from "./shared/tick.js";

// {{game_name}} — jeton de mission (rempli par le worker). Gardé en littéral pour rester tsc-vert avant.
export const gameName = "{{game_name}}";

// État canonique initial d'un run, validé contre le schéma partagé.
export const initial: GameState = GameState.parse(initialGameState({ runSeed: 1 }));
