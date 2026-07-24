// index.ts — entrée TS d'amorçage (univers unifié : client `web/` + serveur autoritatif `server/`). Valide le
// modèle de domaine partagé contre son schéma Zod pour que `tsc --noEmit` (gate Tier-0) ait une entrée à
// vérifier dès la création. Le worker THÉMATISE / ÉQUILIBRE le moteur né-avec (il ne le fonde pas).
import { GameState } from "./shared/schema.js";
import { initialGameState } from "./shared/tick.js";

// {{game_name}} — jeton de mission (rempli par le worker). Gardé en littéral pour rester tsc-vert avant.
export const gameName = "{{game_name}}";

// État canonique initial d'un run, validé contre le schéma partagé.
export const initial: GameState = GameState.parse(initialGameState({ runSeed: 1 }));

// Combat déterministe (F2) — résolveur + bonus de tech. L'APPLICATION du rapport à l'univers est F3.
export { resolveBattle, effectiveStats, MAX_UNITS } from "./shared/combat.js";
export type { Force, BattleReport, BattleContext, ResourceDebris } from "./shared/combat.js";

// Factions PNJ (F2) — politiques de build-order pures, déterministes.
export { botCommands, FACTIONS } from "./shared/bots.js";
export type { FactionArchetype } from "./shared/bots.js";
