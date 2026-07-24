// data/research.ts — catalogue des recherches (données pures, sourcées OGame : Fandom « Research », compendium
// gameguide). Coût `base · costFactor^(L-1)` (× per niveau, astrophysique 1.75). Les techs de combat
// (armes/bouclier/blindage) donnent +10 %/niveau **multiplicatif** sur les stats (appliqué en F2, combat) ;
// les drives pilotent vitesse/carburant (F2, mouvement). Prérequis = niveaux de bâtiments + de recherches.
// (Graviton — coût en ÉNERGIE, pas en ressources — est différé : contenu deathstar, hors 2b.)

export type ResearchId =
  | "energyTech"
  | "laserTech"
  | "ionTech"
  | "hyperspaceTech"
  | "plasmaTech"
  | "espionageTech"
  | "computerTech"
  | "astrophysics"
  | "intergalacticResearchNetwork"
  | "combustionDrive"
  | "impulseDrive"
  | "hyperspaceDrive"
  | "weaponsTech"
  | "shieldingTech"
  | "armourTech";

export type ResourceCost = { metal: number; crystal: number; deuterium: number };

export type ResearchDef = {
  id: ResearchId;
  label: string;
  baseCost: ResourceCost;
  costFactor: number; // coût(L) = base · costFactor^(L-1), floored par ressource
  requiresBuildings: Partial<Record<string, number>>; // niveaux de bâtiments requis (par id de bâtiment)
  requiresResearch: Partial<Record<ResearchId, number>>;
  combatBonus?: "weapon" | "shield" | "armour"; // +10 %/niveau multiplicatif (F2)
  drive?: "combustion" | "impulse" | "hyperspace"; // pilote vitesse/carburant (F2)
};

const c = (metal: number, crystal: number, deuterium: number): ResourceCost => ({ metal, crystal, deuterium });

export const RESEARCH: Record<ResearchId, ResearchDef> = {
  energyTech: {
    id: "energyTech", label: "Technologie Énergétique", baseCost: c(0, 800, 400), costFactor: 2,
    requiresBuildings: { researchLab: 1 }, requiresResearch: {},
  },
  laserTech: {
    id: "laserTech", label: "Technologie Laser", baseCost: c(200, 100, 0), costFactor: 2,
    requiresBuildings: { researchLab: 1 }, requiresResearch: { energyTech: 2 },
  },
  ionTech: {
    id: "ionTech", label: "Technologie à Ions", baseCost: c(1000, 300, 100), costFactor: 2,
    requiresBuildings: { researchLab: 4 }, requiresResearch: { energyTech: 4, laserTech: 5 },
  },
  hyperspaceTech: {
    id: "hyperspaceTech", label: "Technologie Hyperespace", baseCost: c(0, 4000, 2000), costFactor: 2,
    requiresBuildings: { researchLab: 7 }, requiresResearch: { energyTech: 5, shieldingTech: 5 },
  },
  plasmaTech: {
    id: "plasmaTech", label: "Technologie à Plasma", baseCost: c(2000, 4000, 1000), costFactor: 2,
    requiresBuildings: { researchLab: 4 }, requiresResearch: { energyTech: 8, laserTech: 10, ionTech: 5 },
  },
  espionageTech: {
    id: "espionageTech", label: "Technologie d'Espionnage", baseCost: c(200, 1000, 200), costFactor: 2,
    requiresBuildings: { researchLab: 3 }, requiresResearch: {},
  },
  computerTech: {
    id: "computerTech", label: "Technologie Informatique", baseCost: c(0, 400, 600), costFactor: 2,
    requiresBuildings: { researchLab: 1 }, requiresResearch: {},
  },
  astrophysics: {
    id: "astrophysics", label: "Astrophysique", baseCost: c(4000, 8000, 4000), costFactor: 1.75,
    requiresBuildings: { researchLab: 3 }, requiresResearch: { espionageTech: 4, impulseDrive: 3 },
  },
  intergalacticResearchNetwork: {
    id: "intergalacticResearchNetwork", label: "Réseau de Recherche Intergalactique",
    baseCost: c(240000, 400000, 160000), costFactor: 2,
    requiresBuildings: { researchLab: 10 }, requiresResearch: { computerTech: 8, hyperspaceTech: 8 },
  },
  combustionDrive: {
    id: "combustionDrive", label: "Réacteur à Combustion", baseCost: c(400, 0, 600), costFactor: 2,
    requiresBuildings: { researchLab: 1 }, requiresResearch: { energyTech: 1 }, drive: "combustion",
  },
  impulseDrive: {
    id: "impulseDrive", label: "Réacteur à Impulsion", baseCost: c(2000, 4000, 600), costFactor: 2,
    requiresBuildings: { researchLab: 2 }, requiresResearch: { energyTech: 1 }, drive: "impulse",
  },
  hyperspaceDrive: {
    id: "hyperspaceDrive", label: "Propulsion Hyperespace", baseCost: c(10000, 20000, 6000), costFactor: 2,
    requiresBuildings: { researchLab: 7 }, requiresResearch: { hyperspaceTech: 3 }, drive: "hyperspace",
  },
  weaponsTech: {
    id: "weaponsTech", label: "Technologie Militaire", baseCost: c(800, 200, 0), costFactor: 2,
    requiresBuildings: { researchLab: 4 }, requiresResearch: {}, combatBonus: "weapon",
  },
  shieldingTech: {
    id: "shieldingTech", label: "Technologie Bouclier", baseCost: c(200, 600, 0), costFactor: 2,
    requiresBuildings: { researchLab: 6 }, requiresResearch: { energyTech: 3 }, combatBonus: "shield",
  },
  armourTech: {
    id: "armourTech", label: "Technologie Blindage", baseCost: c(1000, 0, 0), costFactor: 2,
    requiresBuildings: { researchLab: 2 }, requiresResearch: {}, combatBonus: "armour",
  },
};

export const RESEARCH_IDS = Object.keys(RESEARCH) as ResearchId[];
