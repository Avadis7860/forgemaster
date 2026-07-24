// bots.ts — politiques des factions PNJ (spec §Touche perso). `botCommands(state, faction, tick)` rend une liste
// de commandes CANDIDATES **priorisées** (unmet-only) — PURE et déterministe. Le driver (serveur, F3) plie
// `applyCommand` sur la liste : `applyCommand` est autoritatif (coût + prérequis), donc le préfixe faisable-et-
// abordable par priorité se construit, le reste est no-op. Les bots n'ont donc PAS à dupliquer la logique de
// coût — la source unique reste `tick.applyCommand`. Croissance déterministe du seed : un bot avancé K ticks via
// `applyTick`+`applyCommand(botCommands(...))` rejoue byte-identique.
//
// F2 = le **cerveau** économique/militaire du bot (que construire). F3 = l'univers qui héberge N empires, le
// mouvement/dispatch entre eux et l'application des `BattleReport`. Ici, aucune notion de cible/combat.
import type { BuildingId } from "./data/buildings.js";
import type { ResearchId } from "./data/research.js";
import type { ShipId } from "./data/ships.js";
import type { DefenseId } from "./data/defense.js";
import type { Command, GameState } from "./schema.js";

export type FactionArchetype = "farmer" | "raider" | "turtle" | "expansionist" | "boss";
export const FACTIONS: readonly FactionArchetype[] = ["farmer", "raider", "turtle", "expansionist", "boss"];

// Un objectif de build-order : bâtiment/recherche jusqu'à un niveau, vaisseau/défense jusqu'à un compte. Le
// « niveau/compte courant » inclut la file (anti-empilement : on ne re-commande pas ce qui est déjà en chantier).
type Goal =
  | readonly ["b", BuildingId, number]
  | readonly ["r", ResearchId, number]
  | readonly ["s", ShipId, number]
  | readonly ["d", DefenseId, number];

function queuedSame(items: GameState["construction"], kind: string, id: string): number {
  return items.reduce((n, q) => (q.kind === kind && q.id === id ? n + 1 : n), 0);
}
function queuedUnits(items: GameState["shipyardQueue"], id: string): number {
  return items.reduce((n, q) => (q.id === id ? n + (q.count ?? 0) : n), 0);
}

// goalCommand — la commande d'enfilement d'un objectif s'il est non atteint (file comprise), sinon `null`.
function goalCommand(state: GameState, g: Goal): Command | null {
  switch (g[0]) {
    case "b": {
      const have = (state.buildings[g[1]] ?? 0) + queuedSame(state.construction, "building", g[1]);
      return have < g[2] ? { kind: "enqueueBuilding", building: g[1] } : null;
    }
    case "r": {
      const have = (state.research[g[1]] ?? 0) + queuedSame(state.labQueue, "research", g[1]);
      return have < g[2] ? { kind: "enqueueResearch", research: g[1] } : null;
    }
    case "s": {
      const have = (state.fleet[g[1]] ?? 0) + queuedUnits(state.shipyardQueue, g[1]);
      return have < g[2] ? { kind: "enqueueShip", ship: g[1], count: 1 } : null;
    }
    case "d": {
      const have = (state.defense[g[1]] ?? 0) + queuedUnits(state.shipyardQueue, g[1]);
      return have < g[2] ? { kind: "enqueueDefense", defense: g[1], count: 1 } : null;
    }
  }
}

function toCommands(state: GameState, goals: readonly Goal[]): Command[] {
  const out: Command[] = [];
  for (const g of goals) {
    const cmd = goalCommand(state, g);
    if (cmd !== null) out.push(cmd);
  }
  return out;
}

// Socle économique commun (énergie d'abord, puis mines, robots, labo) — l'ossature que tout archétype partage.
// Les niveaux sont des **plafonds aspirationnels** : `applyCommand` plafonne par l'économie réelle (les cibles
// hautes = « continue d'améliorer »).
function econBase(metal: number, crystal: number, deut: number): readonly Goal[] {
  return [
    ["b", "solarPlant", metal],
    ["b", "metalMine", metal],
    ["b", "crystalMine", crystal],
    ["b", "deuteriumSynthesizer", deut],
    ["b", "roboticsFactory", 10],
    ["b", "researchLab", 8],
    ["b", "metalStorage", 12],
    ["b", "crystalStorage", 12],
    ["b", "deuteriumTank", 10],
  ];
}

// scaled — un compte militaire qui monte avec l'âge du run (déterministe du tick) : `base + ⌊tick / period⌋`.
function scaled(base: number, tick: number, period: number): number {
  return base + Math.floor(tick / period);
}

// botCommands — la politique d'un archétype. `tick` module l'échelle militaire (flottes qui grossissent avec
// l'âge du run) pour raider/boss.
export function botCommands(state: GameState, faction: FactionArchetype, tick: number): Command[] {
  switch (faction) {
    // Farmer — économie riche, défense-jeton : la cible de raid molle. Un petit chantier rend la défense-jeton
    // ATTEIGNABLE (le lanceur exige `shipyard: 1`) — sans lui l'objectif serait mort (jamais satisfiable).
    case "farmer":
      return toCommands(state, [
        ...econBase(30, 30, 25),
        ["b", "shipyard", 2],
        ["d", "rocketLauncher", 5],
      ]);

    // Raider — économie + flotte offensive (drives, chasseurs/croiseurs) + soutes pour le butin.
    case "raider":
      return toCommands(state, [
        ...econBase(25, 22, 20),
        ["b", "shipyard", 8],
        ["r", "energyTech", 6],
        ["r", "combustionDrive", 6],
        ["r", "impulseDrive", 6],
        ["r", "laserTech", 6],
        ["r", "ionTech", 3],
        ["r", "weaponsTech", 5],
        ["s", "lightFighter", scaled(6, tick, 8)],
        ["s", "cruiser", scaled(2, tick, 15)],
        ["s", "smallCargo", 10],
      ]);

    // Turtle — économie + défense lourde sur un cache : le puzzle de siège.
    case "turtle":
      return toCommands(state, [
        ...econBase(24, 22, 18),
        ["b", "shipyard", 8],
        ["r", "energyTech", 6],
        ["r", "laserTech", 6],
        ["r", "ionTech", 4],
        ["r", "shieldingTech", 6],
        ["r", "weaponsTech", 4],
        ["d", "rocketLauncher", 50],
        ["d", "lightLaser", 40],
        ["d", "heavyLaser", 20],
        ["d", "gaussCannon", 10],
        ["d", "plasmaTurret", 5],
        ["d", "smallShieldDome", 1],
        ["d", "largeShieldDome", 1],
      ]);

    // Expansionist — économie large + recherche (computer/astro) : croissance mole, tentaculaire.
    case "expansionist":
      return toCommands(state, [
        ...econBase(28, 26, 22),
        ["r", "energyTech", 8],
        ["r", "computerTech", 8],
        ["r", "espionageTech", 4],
        ["r", "impulseDrive", 4],
        ["r", "astrophysics", 6],
        ["b", "shipyard", 6],
        ["r", "combustionDrive", 4],
        ["s", "smallCargo", 20],
      ]);

    // Boss — empire deathstar-tier qui grossit : la condition de victoire (doom-clock, F3).
    case "boss":
      return toCommands(state, [
        ...econBase(35, 32, 28),
        ["b", "shipyard", 12],
        ["b", "naniteFactory", 5],
        ["r", "energyTech", 10],
        ["r", "laserTech", 10],
        ["r", "ionTech", 6],
        ["r", "hyperspaceTech", 8],
        ["r", "plasmaTech", 8],
        ["r", "shieldingTech", 8],
        ["r", "weaponsTech", 8],
        ["r", "armourTech", 8],
        ["r", "combustionDrive", 8],
        ["r", "impulseDrive", 8],
        ["r", "hyperspaceDrive", 8],
        ["s", "battleship", scaled(4, tick, 10)],
        ["s", "destroyer", scaled(2, tick, 20)],
        ["s", "deathstar", scaled(0, tick, 60)],
        ["d", "plasmaTurret", 20],
        ["d", "largeShieldDome", 1],
      ]);
  }
}
