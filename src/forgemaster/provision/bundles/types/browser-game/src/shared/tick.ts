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
