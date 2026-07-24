// data/ships.ts — catalogue de la flotte (données pures, sourcées OGame : Fandom « Ships » + « Rapid Fire »,
// compendium gameguide). Stats de COMBAT de base (avant techs armes/bouclier/blindage, appliquées par
// `combat.effectiveStats`) :
//   - coque   `= (metalCost + crystalCost) / 10` (dérivée du coût, cf. formulas.hull) ;
//   - `weapon`/`shield` de base ; `cargo` (capacité de fret) ; `speed`/`fuel` + `drive` (mouvement, F3).
// La **matrice rapidfire** (`rapidfire`) est remplie ici (2ᵉ passe sourcée, F2) : `{ cibleId → compte r }` — le
// tireur relance un tir sur une nouvelle cible avec proba `(r−1)/r` (cf. `combat.resolveBattle`). Restreinte aux
// ids **modélisés** : les cibles OGame non portées (satellite solaire, foreur/crawler) sont omises. Variante
// contestée notée : le RF du bombardier contre le lanceur de plasma (5) diffère selon les univers.
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
  rapidfire: Partial<Record<string, number>>; // { cibleId → r } — sourcé Fandom « Rapid Fire » (F2)
};

const c = (metal: number, crystal: number, deuterium: number): ResourceCost => ({ metal, crystal, deuterium });

export const SHIPS: Record<ShipId, CombatUnitDef> = {
  smallCargo: {
    label: "Petit Transporteur", baseCost: c(2000, 2000, 0), weapon: 5, shield: 10, cargo: 5000,
    speed: 5000, fuel: 10, drive: "combustion",
    requiresBuildings: { shipyard: 2 }, requiresResearch: { combustionDrive: 2 },
    rapidfire: { espionageProbe: 5 },
  },
  largeCargo: {
    label: "Grand Transporteur", baseCost: c(6000, 6000, 0), weapon: 5, shield: 25, cargo: 25000,
    speed: 7500, fuel: 50, drive: "combustion",
    requiresBuildings: { shipyard: 4 }, requiresResearch: { combustionDrive: 6 },
    rapidfire: { espionageProbe: 5 },
  },
  lightFighter: {
    label: "Chasseur Léger", baseCost: c(3000, 1000, 0), weapon: 50, shield: 10, cargo: 50,
    speed: 12500, fuel: 20, drive: "combustion",
    requiresBuildings: { shipyard: 1 }, requiresResearch: { combustionDrive: 1 },
    rapidfire: { espionageProbe: 5 },
  },
  heavyFighter: {
    label: "Chasseur Lourd", baseCost: c(6000, 4000, 0), weapon: 150, shield: 25, cargo: 100,
    speed: 10000, fuel: 75, drive: "impulse",
    requiresBuildings: { shipyard: 3 }, requiresResearch: { armourTech: 2, impulseDrive: 2 },
    rapidfire: { espionageProbe: 5, smallCargo: 3 },
  },
  cruiser: {
    label: "Croiseur", baseCost: c(20000, 7000, 2000), weapon: 400, shield: 50, cargo: 800,
    speed: 15000, fuel: 300, drive: "impulse",
    requiresBuildings: { shipyard: 5 }, requiresResearch: { impulseDrive: 4, ionTech: 2 },
    rapidfire: { espionageProbe: 5, lightFighter: 6, rocketLauncher: 10 },
  },
  battleship: {
    label: "Vaisseau de Bataille", baseCost: c(45000, 15000, 0), weapon: 1000, shield: 200, cargo: 1500,
    speed: 10000, fuel: 500, drive: "hyperspace",
    requiresBuildings: { shipyard: 7 }, requiresResearch: { hyperspaceDrive: 4 },
    rapidfire: { espionageProbe: 5 },
  },
  battlecruiser: {
    label: "Traqueur", baseCost: c(30000, 40000, 15000), weapon: 700, shield: 400, cargo: 750,
    speed: 10000, fuel: 250, drive: "hyperspace",
    requiresBuildings: { shipyard: 8 },
    requiresResearch: { hyperspaceTech: 5, hyperspaceDrive: 5, laserTech: 12 },
    rapidfire: { espionageProbe: 5, smallCargo: 3, largeCargo: 3, heavyFighter: 4, cruiser: 4, battleship: 7 },
  },
  bomber: {
    label: "Bombardier", baseCost: c(50000, 25000, 15000), weapon: 1000, shield: 500, cargo: 500,
    speed: 4000, fuel: 1000, drive: "impulse",
    requiresBuildings: { shipyard: 8 }, requiresResearch: { impulseDrive: 6, plasmaTech: 5 },
    rapidfire: {
      espionageProbe: 5, rocketLauncher: 20, lightLaser: 20, heavyLaser: 10, ionCannon: 10,
      gaussCannon: 5, plasmaTurret: 5,
    },
  },
  destroyer: {
    label: "Destructeur", baseCost: c(60000, 50000, 15000), weapon: 2000, shield: 500, cargo: 2000,
    speed: 5000, fuel: 1000, drive: "hyperspace",
    requiresBuildings: { shipyard: 9 }, requiresResearch: { hyperspaceTech: 5, hyperspaceDrive: 6 },
    rapidfire: { espionageProbe: 5, lightLaser: 10, battlecruiser: 2 },
  },
  deathstar: {
    label: "Étoile de la Mort", baseCost: c(5000000, 4000000, 1000000), weapon: 200000, shield: 50000,
    cargo: 1000000, speed: 100, fuel: 1, drive: "hyperspace",
    requiresBuildings: { shipyard: 12 }, requiresResearch: { hyperspaceTech: 6, hyperspaceDrive: 7 },
    rapidfire: {
      espionageProbe: 1250, smallCargo: 250, largeCargo: 250, lightFighter: 200, heavyFighter: 100,
      cruiser: 33, battleship: 30, battlecruiser: 15, bomber: 25, destroyer: 5, recycler: 250,
      rocketLauncher: 200, lightLaser: 200, heavyLaser: 100, ionCannon: 100, gaussCannon: 50,
    },
  },
  recycler: {
    label: "Recycleur", baseCost: c(10000, 6000, 2000), weapon: 1, shield: 10, cargo: 20000,
    speed: 2000, fuel: 300, drive: "combustion",
    requiresBuildings: { shipyard: 4 }, requiresResearch: { combustionDrive: 6, shieldingTech: 2 },
    rapidfire: { espionageProbe: 5 },
  },
  espionageProbe: {
    label: "Sonde d'Espionnage", baseCost: c(0, 1000, 0), weapon: 0, shield: 0, cargo: 5,
    speed: 100000000, fuel: 1, drive: "combustion",
    requiresBuildings: { shipyard: 3 }, requiresResearch: { combustionDrive: 3, espionageTech: 2 },
    rapidfire: {},
  },
};

export const SHIP_IDS = Object.keys(SHIPS) as ShipId[];
