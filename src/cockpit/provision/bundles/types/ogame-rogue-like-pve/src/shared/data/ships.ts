// data/ships.ts — catalogue de la flotte (données pures, sourcées OGame : Fandom « Ships », compendium
// gameguide). Stats de COMBAT de base (avant techs armes/bouclier/blindage, appliquées en F2) :
//   - coque   `= (metalCost + crystalCost) / 10` (dérivée du coût, cf. formulas.hull) ;
//   - `weapon`/`shield` de base ; `cargo` (capacité de fret) ; `speed`/`fuel` + `drive` (mouvement, F2).
// La **matrice rapidfire** (`rapidfire`) est la partie contestée notée pour une 2ᵉ passe → remplie en F2
// (combat). Ici elle reste vide : ce fichier fixe le domaine buildable (coût, prérequis) né-avec.
import type { ResearchId, ResourceCost } from "./research.js";
import type { BuildingId } from "./buildings.js";

export type ShipId =
  | "smallCargo"
  | "largeCargo"
  | "lightFighter"
  | "heavyFighter"
  | "cruiser"
  | "battleship"
  | "battlecruiser"
  | "bomber"
  | "destroyer"
  | "deathstar"
  | "recycler"
  | "espionageProbe";

export type CombatUnitDef = {
  label: string;
  baseCost: ResourceCost;
  weapon: number;
  shield: number;
  cargo: number; // capacité de fret (0 pour la défense)
  speed: number; // vitesse de base (drive de départ)
  fuel: number; // conso deutérium de base
  drive?: "combustion" | "impulse" | "hyperspace";
  requiresBuildings: Partial<Record<BuildingId, number>>;
  requiresResearch: Partial<Record<ResearchId, number>>;
  rapidfire: Partial<Record<string, number>>; // rempli en F2 (combat) — 2ᵉ passe sourcée
};

const c = (metal: number, crystal: number, deuterium: number): ResourceCost => ({ metal, crystal, deuterium });

export const SHIPS: Record<ShipId, CombatUnitDef> = {
  smallCargo: {
    label: "Petit Transporteur", baseCost: c(2000, 2000, 0), weapon: 5, shield: 10, cargo: 5000,
    speed: 5000, fuel: 10, drive: "combustion",
    requiresBuildings: { shipyard: 2 }, requiresResearch: { combustionDrive: 2 }, rapidfire: {},
  },
  largeCargo: {
    label: "Grand Transporteur", baseCost: c(6000, 6000, 0), weapon: 5, shield: 25, cargo: 25000,
    speed: 7500, fuel: 50, drive: "combustion",
    requiresBuildings: { shipyard: 4 }, requiresResearch: { combustionDrive: 6 }, rapidfire: {},
  },
  lightFighter: {
    label: "Chasseur Léger", baseCost: c(3000, 1000, 0), weapon: 50, shield: 10, cargo: 50,
    speed: 12500, fuel: 20, drive: "combustion",
    requiresBuildings: { shipyard: 1 }, requiresResearch: { combustionDrive: 1 }, rapidfire: {},
  },
  heavyFighter: {
    label: "Chasseur Lourd", baseCost: c(6000, 4000, 0), weapon: 150, shield: 25, cargo: 100,
    speed: 10000, fuel: 75, drive: "impulse",
    requiresBuildings: { shipyard: 3 }, requiresResearch: { armourTech: 2, impulseDrive: 2 }, rapidfire: {},
  },
  cruiser: {
    label: "Croiseur", baseCost: c(20000, 7000, 2000), weapon: 400, shield: 50, cargo: 800,
    speed: 15000, fuel: 300, drive: "impulse",
    requiresBuildings: { shipyard: 5 }, requiresResearch: { impulseDrive: 4, ionTech: 2 }, rapidfire: {},
  },
  battleship: {
    label: "Vaisseau de Bataille", baseCost: c(45000, 15000, 0), weapon: 1000, shield: 200, cargo: 1500,
    speed: 10000, fuel: 500, drive: "hyperspace",
    requiresBuildings: { shipyard: 7 }, requiresResearch: { hyperspaceDrive: 4 }, rapidfire: {},
  },
  battlecruiser: {
    label: "Traqueur", baseCost: c(30000, 40000, 15000), weapon: 700, shield: 400, cargo: 750,
    speed: 10000, fuel: 250, drive: "hyperspace",
    requiresBuildings: { shipyard: 8 },
    requiresResearch: { hyperspaceTech: 5, hyperspaceDrive: 5, laserTech: 12 }, rapidfire: {},
  },
  bomber: {
    label: "Bombardier", baseCost: c(50000, 25000, 15000), weapon: 1000, shield: 500, cargo: 500,
    speed: 4000, fuel: 1000, drive: "impulse",
    requiresBuildings: { shipyard: 8 }, requiresResearch: { impulseDrive: 6, plasmaTech: 5 }, rapidfire: {},
  },
  destroyer: {
    label: "Destructeur", baseCost: c(60000, 50000, 15000), weapon: 2000, shield: 500, cargo: 2000,
    speed: 5000, fuel: 1000, drive: "hyperspace",
    requiresBuildings: { shipyard: 9 }, requiresResearch: { hyperspaceTech: 5, hyperspaceDrive: 6 }, rapidfire: {},
  },
  deathstar: {
    label: "Étoile de la Mort", baseCost: c(5000000, 4000000, 1000000), weapon: 200000, shield: 50000,
    cargo: 1000000, speed: 100, fuel: 1, drive: "hyperspace",
    requiresBuildings: { shipyard: 12 }, requiresResearch: { hyperspaceTech: 6, hyperspaceDrive: 7 }, rapidfire: {},
  },
  recycler: {
    label: "Recycleur", baseCost: c(10000, 6000, 2000), weapon: 1, shield: 10, cargo: 20000,
    speed: 2000, fuel: 300, drive: "combustion",
    requiresBuildings: { shipyard: 4 }, requiresResearch: { combustionDrive: 6, shieldingTech: 2 }, rapidfire: {},
  },
  espionageProbe: {
    label: "Sonde d'Espionnage", baseCost: c(0, 1000, 0), weapon: 0, shield: 0, cargo: 5,
    speed: 100000000, fuel: 1, drive: "combustion",
    requiresBuildings: { shipyard: 3 }, requiresResearch: { combustionDrive: 3, espionageTech: 2 }, rapidfire: {},
  },
};

export const SHIP_IDS = Object.keys(SHIPS) as ShipId[];
