// data/defense.ts — catalogue de la défense planétaire (données pures, sourcées OGame : Fandom « Defense »).
// Réutilise `CombatUnitDef` (coque `= (metal+crystal)/10`, `weapon`/`shield`, `cargo`=0). Réparée à ~70 %
// après combat (`combat.resolveBattle` → `defenseRepair`). Les dômes de bouclier sont **uniques**
// (`maxCount: 1`). Missiles (ABM/IPM) différés. La défense **n'a pas de rapidfire** en OGame (sourcé) : chaque
// `rapidfire` reste `{}` — volontaire, pas un trou. Ce sont les vaisseaux qui rapidfirent la défense (cf. ships).
import type { CombatUnitDef } from "./ships.js";
import type { ResourceCost } from "./research.js";

export type DefenseId =
  | "rocketLauncher"
  | "lightLaser"
  | "heavyLaser"
  | "ionCannon"
  | "gaussCannon"
  | "plasmaTurret"
  | "smallShieldDome"
  | "largeShieldDome";

export type DefenseDef = CombatUnitDef & { maxCount?: number };

const c = (metal: number, crystal: number, deuterium: number): ResourceCost => ({ metal, crystal, deuterium });

export const DEFENSES: Record<DefenseId, DefenseDef> = {
  rocketLauncher: {
    label: "Lanceur de Missiles", baseCost: c(2000, 0, 0), weapon: 80, shield: 20, cargo: 0,
    speed: 0, fuel: 0, requiresBuildings: { shipyard: 1 }, requiresResearch: {}, rapidfire: {},
  },
  lightLaser: {
    label: "Artillerie Laser Légère", baseCost: c(1500, 500, 0), weapon: 100, shield: 25, cargo: 0,
    speed: 0, fuel: 0, requiresBuildings: { shipyard: 2 }, requiresResearch: { energyTech: 1, laserTech: 3 }, rapidfire: {},
  },
  heavyLaser: {
    label: "Artillerie Laser Lourde", baseCost: c(6000, 2000, 0), weapon: 250, shield: 100, cargo: 0,
    speed: 0, fuel: 0, requiresBuildings: { shipyard: 4 }, requiresResearch: { energyTech: 3, laserTech: 6 }, rapidfire: {},
  },
  ionCannon: {
    label: "Canon à Ions", baseCost: c(5000, 3000, 0), weapon: 150, shield: 500, cargo: 0,
    speed: 0, fuel: 0, requiresBuildings: { shipyard: 4 }, requiresResearch: { ionTech: 4 }, rapidfire: {},
  },
  gaussCannon: {
    label: "Canon de Gauss", baseCost: c(20000, 15000, 2000), weapon: 1100, shield: 200, cargo: 0,
    speed: 0, fuel: 0, requiresBuildings: { shipyard: 6 },
    requiresResearch: { energyTech: 6, weaponsTech: 3, shieldingTech: 1 }, rapidfire: {},
  },
  plasmaTurret: {
    label: "Lanceur de Plasma", baseCost: c(50000, 50000, 30000), weapon: 3000, shield: 300, cargo: 0,
    speed: 0, fuel: 0, requiresBuildings: { shipyard: 8 }, requiresResearch: { plasmaTech: 7 }, rapidfire: {},
  },
  smallShieldDome: {
    label: "Petit Bouclier", baseCost: c(10000, 10000, 0), weapon: 1, shield: 2000, cargo: 0,
    speed: 0, fuel: 0, maxCount: 1, requiresBuildings: { shipyard: 1 }, requiresResearch: { shieldingTech: 2 }, rapidfire: {},
  },
  largeShieldDome: {
    label: "Grand Bouclier", baseCost: c(50000, 50000, 0), weapon: 1, shield: 10000, cargo: 0,
    speed: 0, fuel: 0, maxCount: 1, requiresBuildings: { shipyard: 6 }, requiresResearch: { shieldingTech: 6 }, rapidfire: {},
  },
};

export const DEFENSE_IDS = Object.keys(DEFENSES) as DefenseId[];
