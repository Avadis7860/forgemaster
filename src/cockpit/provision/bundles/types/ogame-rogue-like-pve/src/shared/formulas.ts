// formulas.ts — les vraies formules OGame (sourcées : Fandom « Formulas », compendium gameguide), en math
// **floorée** déterministe (aucun bruit flottant bas de gamme ne fuit dans l'état). Pures, sans I/O. Elles
// sont la source unique des coûts / production / temps → testées par oracle (`formulas.test.ts`).
//
//   coût(L)         = base · costFactor^(L-1)                         (par ressource, floored)
//   prod mine/h     = prodBase · L · 1.1^L · universeSpeed           (deut × (-0.002·T + 1.28))
//   énergie         = energyBase · L · 1.1^L                          (NON multipliée par universeSpeed)
//   stockage        = 5000 · ⌊2.5 · e^(20L/33)⌋
//   coque           = ⌊(metal + crystal) / 10⌋
//   temps (ticks)   = ⌊(metal + crystal) / (K · (1 + facilityL) · 2^nanite · universeSpeed)⌋   (min 1)
import { BUILDINGS, type BuildingId } from "./data/buildings.js";
import { RESEARCH, type ResearchId, type ResourceCost } from "./data/research.js";
import { SHIPS, type ShipId } from "./data/ships.js";
import { DEFENSES, type DefenseId } from "./data/defense.js";

export const PRODUCTION_POWER = 1.1;
export const ENERGY_POWER = 1.1;

// scaleCost — coût d'un niveau depuis le coût de base et le facteur. `level` est le niveau VISÉ (≥ 1).
export function scaleCost(base: ResourceCost, factor: number, level: number): ResourceCost {
  const m = Math.pow(factor, level - 1);
  return {
    metal: Math.floor(base.metal * m),
    crystal: Math.floor(base.crystal * m),
    deuterium: Math.floor(base.deuterium * m),
  };
}

export function buildingCost(id: BuildingId, level: number): ResourceCost {
  const d = BUILDINGS[id];
  return scaleCost(d.baseCost, d.costFactor, level);
}

export function researchCost(id: ResearchId, level: number): ResourceCost {
  const d = RESEARCH[id];
  return scaleCost(d.baseCost, d.costFactor, level);
}

export function shipCost(id: ShipId): ResourceCost {
  return { ...SHIPS[id].baseCost };
}

export function defenseCost(id: DefenseId): ResourceCost {
  return { ...DEFENSES[id].baseCost };
}

export type ProductionOpts = { universeSpeed: number; temperature: number };

// mineProduction — production/h d'une mine à un niveau (0 si pas une mine ou niveau 0). Le deutérium dépend de
// la température (`-0.002·T + 1.28`, T = température max de la planète).
export function mineProduction(id: BuildingId, level: number, opts: ProductionOpts): number {
  const d = BUILDINGS[id];
  if (d.role !== "mine" || d.prodBase === undefined || level <= 0) return 0;
  let p = d.prodBase * level * Math.pow(PRODUCTION_POWER, level) * opts.universeSpeed;
  if (d.produces === "deuterium") p *= -0.002 * opts.temperature + 1.28;
  return Math.floor(p);
}

// energyProduction — énergie produite par un bâtiment `energy` (solaire). NON multipliée par universeSpeed.
export function energyProduction(id: BuildingId, level: number): number {
  const d = BUILDINGS[id];
  if (d.role !== "energy" || d.energyBase === undefined || level <= 0) return 0;
  return Math.floor(d.energyBase * level * Math.pow(ENERGY_POWER, level));
}

// energyConsumption — énergie consommée par une mine à un niveau.
export function energyConsumption(id: BuildingId, level: number): number {
  const d = BUILDINGS[id];
  if (d.role !== "mine" || d.energyBase === undefined || level <= 0) return 0;
  return Math.floor(d.energyBase * level * Math.pow(ENERGY_POWER, level));
}

// storageCapacity — capacité d'un hangar/réservoir à un niveau (niveau 0 = 10000).
export function storageCapacity(level: number): number {
  return 5000 * Math.floor(2.5 * Math.exp((20 * level) / 33));
}

// hull — points de structure d'une unité de combat, dérivés de son coût.
export function hull(cost: ResourceCost): number {
  return Math.floor((cost.metal + cost.crystal) / 10);
}

const BUILD_K = 2500; // constante OGame du temps de construction (bâtiments + flotte)
const RESEARCH_K = 1000; // constante du temps de recherche

// buildTimeTicks — temps de construction d'un bâtiment en TICKS (≈ heures), réduit par l'usine de robots
// (1 + niveau) et l'usine de nanites (2^niveau), et par universeSpeed. Min 1 tick.
export function buildTimeTicks(
  cost: ResourceCost, roboticsLevel: number, naniteLevel: number, universeSpeed: number,
): number {
  const hours = (cost.metal + cost.crystal)
    / (BUILD_K * (1 + roboticsLevel) * Math.pow(2, naniteLevel) * universeSpeed);
  return Math.max(1, Math.floor(hours));
}

// researchTimeTicks — temps de recherche en ticks, réduit par le laboratoire (1 + niveau) et universeSpeed.
export function researchTimeTicks(cost: ResourceCost, researchLabLevel: number, universeSpeed: number): number {
  const hours = (cost.metal + cost.crystal) / (RESEARCH_K * (1 + researchLabLevel) * universeSpeed);
  return Math.max(1, Math.floor(hours));
}

// shipyardTimeTicks — temps de construction d'une unité (vaisseau/défense) en ticks, réduit par le chantier
// (1 + niveau) et les nanites (2^niveau). Pour une SÉRIE de N unités, multiplie par N à l'appelant.
export function shipyardTimeTicks(
  cost: ResourceCost, shipyardLevel: number, naniteLevel: number, universeSpeed: number,
): number {
  const hours = (cost.metal + cost.crystal)
    / (BUILD_K * (1 + shipyardLevel) * Math.pow(2, naniteLevel) * universeSpeed);
  return Math.max(1, Math.floor(hours));
}
