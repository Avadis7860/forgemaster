// data/buildings.ts — catalogue des bâtiments (données pures, sourcées OGame : Fandom « Buildings »/« Supplies »,
// compendium gameguide). Coût `base · costFactor^(L-1)` (mines 1.5/1.6, reste ×2). Rôle économique :
//   - `mine`    → produit une ressource : prod/h `= prodBase · L · 1.1^L` ; consomme `energyBase · L · 1.1^L`.
//   - `energy`  → produit de l'énergie : `energyBase · L · 1.1^L`.
//   - `storage` → capacité `= 5000 · ⌊2.5 · e^(20L/33)⌋` pour `storageResource`.
//   - `facility`→ robotics/nanite (réduisent le temps de construction), shipyard, researchLab (prérequis).
// (Fusion/satellites solaires différés : énergie = solaire seul en 2b — twist roguelike, pas tout OGame.)
import type { ResearchId, ResourceCost } from "./research.js";

export type BuildingId =
  | "metalMine"
  | "crystalMine"
  | "deuteriumSynthesizer"
  | "solarPlant"
  | "metalStorage"
  | "crystalStorage"
  | "deuteriumTank"
  | "roboticsFactory"
  | "shipyard"
  | "researchLab"
  | "naniteFactory";

export type ResourceKind = "metal" | "crystal" | "deuterium";
export type BuildingRole = "mine" | "energy" | "storage" | "facility";

export type BuildingDef = {
  id: BuildingId;
  label: string;
  baseCost: ResourceCost;
  costFactor: number;
  role: BuildingRole;
  produces?: ResourceKind; // pour `mine`
  prodBase?: number; // pour `mine` : base de production/h
  energyBase?: number; // `mine` : conso base ; `energy` : production base
  storageResource?: ResourceKind; // pour `storage`
  requiresBuildings: Partial<Record<BuildingId, number>>;
  requiresResearch: Partial<Record<ResearchId, number>>;
};

const c = (metal: number, crystal: number, deuterium: number): ResourceCost => ({ metal, crystal, deuterium });

export const BUILDINGS: Record<BuildingId, BuildingDef> = {
  metalMine: {
    id: "metalMine", label: "Mine de Métal", baseCost: c(60, 15, 0), costFactor: 1.5,
    role: "mine", produces: "metal", prodBase: 30, energyBase: 10,
    requiresBuildings: {}, requiresResearch: {},
  },
  crystalMine: {
    id: "crystalMine", label: "Mine de Cristal", baseCost: c(48, 24, 0), costFactor: 1.6,
    role: "mine", produces: "crystal", prodBase: 20, energyBase: 10,
    requiresBuildings: {}, requiresResearch: {},
  },
  deuteriumSynthesizer: {
    id: "deuteriumSynthesizer", label: "Synthétiseur de Deutérium", baseCost: c(225, 75, 0), costFactor: 1.5,
    role: "mine", produces: "deuterium", prodBase: 10, energyBase: 20,
    requiresBuildings: {}, requiresResearch: {},
  },
  solarPlant: {
    id: "solarPlant", label: "Centrale Solaire", baseCost: c(75, 30, 0), costFactor: 1.5,
    role: "energy", energyBase: 20,
    requiresBuildings: {}, requiresResearch: {},
  },
  metalStorage: {
    id: "metalStorage", label: "Hangar de Métal", baseCost: c(1000, 0, 0), costFactor: 2,
    role: "storage", storageResource: "metal",
    requiresBuildings: {}, requiresResearch: {},
  },
  crystalStorage: {
    id: "crystalStorage", label: "Hangar de Cristal", baseCost: c(1000, 500, 0), costFactor: 2,
    role: "storage", storageResource: "crystal",
    requiresBuildings: {}, requiresResearch: {},
  },
  deuteriumTank: {
    id: "deuteriumTank", label: "Réservoir de Deutérium", baseCost: c(1000, 1000, 0), costFactor: 2,
    role: "storage", storageResource: "deuterium",
    requiresBuildings: {}, requiresResearch: {},
  },
  roboticsFactory: {
    id: "roboticsFactory", label: "Usine de Robots", baseCost: c(400, 120, 200), costFactor: 2,
    role: "facility",
    requiresBuildings: {}, requiresResearch: {},
  },
  shipyard: {
    id: "shipyard", label: "Chantier Spatial", baseCost: c(400, 200, 100), costFactor: 2,
    role: "facility",
    requiresBuildings: { roboticsFactory: 2 }, requiresResearch: {},
  },
  researchLab: {
    id: "researchLab", label: "Laboratoire de Recherche", baseCost: c(200, 400, 200), costFactor: 2,
    role: "facility",
    requiresBuildings: {}, requiresResearch: {},
  },
  naniteFactory: {
    id: "naniteFactory", label: "Usine de Nanites", baseCost: c(1000000, 500000, 100000), costFactor: 2,
    role: "facility",
    requiresBuildings: { roboticsFactory: 10 }, requiresResearch: { computerTech: 10 },
  },
};

export const BUILDING_IDS = Object.keys(BUILDINGS) as BuildingId[];
