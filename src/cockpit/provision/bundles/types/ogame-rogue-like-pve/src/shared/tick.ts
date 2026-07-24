// tick.ts — cœur DÉTERMINISTE partagé de la simulation ogame : réducteurs PURS, sans I/O ni UI (décision 5 :
// la résolution se teste avant l'écran). Univers TS unique (décision 1) → vit en `shared` ; l'AUTORITÉ reste
// serveur (décision 2) : SEUL le serveur exécute ces réducteurs sur l'état canonique. Deux réducteurs :
//   - `applyTick(state)`    : avance d'un tick — production (mines × ratio d'énergie + revenu de base, cappée
//                             au stockage) puis achèvements de files (bâtiments/recherche/chantier).
//   - `applyCommand(state,cmd)` : enfile un bâtiment/recherche/vaisseau/défense — prérequis + coût vérifiés,
//                             ressources débitées à l'enfilement (modèle OGame), refus = état inchangé (même
//                             référence). Totaux : ne lèvent jamais.
import { BUILDINGS, BUILDING_IDS, type BuildingId, type ResourceKind } from "./data/buildings.js";
import { RESEARCH, type ResearchId } from "./data/research.js";
import { SHIPS, type ShipId } from "./data/ships.js";
import { DEFENSES, type DefenseId } from "./data/defense.js";
import type { ResourceCost } from "./data/research.js";
import * as F from "./formulas.js";
import { Command, type Coordinates, type CountMap, type GameState, type QueueItem, type Resources } from "./schema.js";

// Revenu de base planétaire par tick (indépendant des mines, ×universeSpeed) — garde un run jouable dès L0.
const BASE_INCOME: Record<ResourceKind, number> = { metal: 30, crystal: 15, deuterium: 0 };

// initialGameState — état canonique d'un run frais (contenu neutre paramétrable). 500/500 comme OGame.
export function initialGameState(
  opts: Partial<{ runSeed: number; universeSpeed: number; coords: Coordinates; temperature: number }> = {},
): GameState {
  return {
    tick: 0,
    universeSpeed: opts.universeSpeed ?? 1,
    runSeed: opts.runSeed ?? 1,
    planet: { coords: opts.coords ?? { galaxy: 1, system: 1, position: 1 }, temperature: opts.temperature ?? 20 },
    resources: { metal: 500, crystal: 500, deuterium: 0 },
    buildings: {},
    research: {},
    fleet: {},
    defense: {},
    construction: [],
    labQueue: [],
    shipyardQueue: [],
  };
}

// ---- helpers purs -----------------------------------------------------------------------------------------

function lvl(map: CountMap, id: string): number {
  return map[id] ?? 0;
}

function requirementsMet(
  buildings: CountMap, research: CountMap,
  reqB: Partial<Record<string, number>>, reqR: Partial<Record<string, number>>,
): boolean {
  for (const [id, need] of Object.entries(reqB)) if (lvl(buildings, id) < (need ?? 0)) return false;
  for (const [id, need] of Object.entries(reqR)) if (lvl(research, id) < (need ?? 0)) return false;
  return true;
}

function canAfford(res: Resources, cost: ResourceCost): boolean {
  return res.metal >= cost.metal && res.crystal >= cost.crystal && res.deuterium >= cost.deuterium;
}

function debit(res: Resources, cost: ResourceCost): Resources {
  return {
    metal: res.metal - cost.metal,
    crystal: res.crystal - cost.crystal,
    deuterium: res.deuterium - cost.deuterium,
  };
}

function scaleCostBy(cost: ResourceCost, n: number): ResourceCost {
  return { metal: cost.metal * n, crystal: cost.crystal * n, deuterium: cost.deuterium * n };
}

function pendingSameKind(queue: QueueItem[], kind: QueueItem["kind"], id: string): number {
  return queue.filter((q) => q.kind === kind && q.id === id).length;
}

function pendingUnitCount(queue: QueueItem[], id: string): number {
  return queue.reduce((n, q) => (q.id === id ? n + (q.count ?? 0) : n), 0);
}

// queueTail — tick à partir duquel le PROCHAIN item de la file démarrera (séquentiel : après la queue).
function queueTail(queue: QueueItem[], tick: number): number {
  return queue.length === 0 ? tick : queue[queue.length - 1].completesAtTick;
}

// ---- production -------------------------------------------------------------------------------------------

function storageCap(buildings: CountMap, resource: ResourceKind): number {
  const id = resource === "metal" ? "metalStorage" : resource === "crystal" ? "crystalStorage" : "deuteriumTank";
  return F.storageCapacity(lvl(buildings, id));
}

// energyRatio — bilan d'énergie : si la conso dépasse la production, TOUTES les mines sont scalées au ratio
// `produced/consumed` (< 1). Sinon 1. Déterministe (float stable → même floor).
function energyRatio(buildings: CountMap): number {
  let produced = 0;
  let consumed = 0;
  for (const id of BUILDING_IDS) {
    const l = lvl(buildings, id);
    produced += F.energyProduction(id, l);
    consumed += F.energyConsumption(id, l);
  }
  if (consumed === 0) return 1;
  return Math.min(1, produced / consumed);
}

// addCapped — ajoute `gain` sans dépasser `cap` (stockage) et sans jamais perdre l'existant (si déjà > cap).
function addCapped(current: number, gain: number, cap: number): number {
  const next = current + gain;
  if (next <= cap) return next;
  return current >= cap ? current : cap;
}

// drainQueue — applique les items dont `completesAtTick <= t` (tête de file, ordre stable) via `apply`, et rend
// la file restante. Préserve la référence si rien n'est drainé (pureté / égalité référentielle).
function drainQueue(queue: QueueItem[], t: number, apply: (item: QueueItem) => void): QueueItem[] {
  let i = 0;
  while (i < queue.length && queue[i].completesAtTick <= t) {
    apply(queue[i]);
    i++;
  }
  return i === 0 ? queue : queue.slice(i);
}

// applyTick — avance d'un tick. Production calculée sur les niveaux AVANT achèvements (un bâtiment fini profite
// au tick SUIVANT, comme OGame). Pur : ne mute pas `state`.
export function applyTick(state: GameState): GameState {
  const t = state.tick + 1;
  const speed = state.universeSpeed;
  const ratio = energyRatio(state.buildings);
  const opts = { universeSpeed: speed, temperature: state.planet.temperature };

  const mineFor = (resource: ResourceKind): number => {
    let sum = 0;
    for (const id of BUILDING_IDS) {
      if (BUILDINGS[id].produces === resource) sum += F.mineProduction(id, lvl(state.buildings, id), opts);
    }
    return Math.floor(sum * ratio);
  };

  const resources: Resources = {
    metal: addCapped(state.resources.metal, BASE_INCOME.metal * speed + mineFor("metal"),
      storageCap(state.buildings, "metal")),
    crystal: addCapped(state.resources.crystal, BASE_INCOME.crystal * speed + mineFor("crystal"),
      storageCap(state.buildings, "crystal")),
    deuterium: addCapped(state.resources.deuterium, BASE_INCOME.deuterium * speed + mineFor("deuterium"),
      storageCap(state.buildings, "deuterium")),
  };

  const buildings = { ...state.buildings };
  const research = { ...state.research };
  const fleet = { ...state.fleet };
  const defense = { ...state.defense };

  const construction = drainQueue(state.construction, t, (item) => {
    if (item.targetLevel !== undefined) buildings[item.id] = item.targetLevel;
  });
  const labQueue = drainQueue(state.labQueue, t, (item) => {
    if (item.targetLevel !== undefined) research[item.id] = item.targetLevel;
  });
  const shipyardQueue = drainQueue(state.shipyardQueue, t, (item) => {
    if (item.kind === "ship") fleet[item.id] = lvl(fleet, item.id) + (item.count ?? 0);
    else if (item.kind === "defense") defense[item.id] = lvl(defense, item.id) + (item.count ?? 0);
  });

  return { ...state, tick: t, resources, buildings, research, fleet, defense, construction, labQueue, shipyardQueue };
}

// ---- commandes (serveur-autoritatif) ----------------------------------------------------------------------

function enqueueBuilding(state: GameState, building: string): GameState {
  if (!(building in BUILDINGS)) return state;
  const def = BUILDINGS[building as BuildingId];
  if (!requirementsMet(state.buildings, state.research, def.requiresBuildings, def.requiresResearch)) return state;
  const targetLevel = lvl(state.buildings, building) + pendingSameKind(state.construction, "building", building) + 1;
  const cost = F.buildingCost(building as BuildingId, targetLevel);
  if (!canAfford(state.resources, cost)) return state;
  const startTick = queueTail(state.construction, state.tick);
  const duration = F.buildTimeTicks(
    cost, lvl(state.buildings, "roboticsFactory"), lvl(state.buildings, "naniteFactory"), state.universeSpeed);
  const item: QueueItem = {
    kind: "building", id: building, targetLevel, startedAtTick: startTick, completesAtTick: startTick + duration,
  };
  return { ...state, resources: debit(state.resources, cost), construction: [...state.construction, item] };
}

function enqueueResearch(state: GameState, research: string): GameState {
  if (!(research in RESEARCH)) return state;
  const def = RESEARCH[research as ResearchId];
  if (!requirementsMet(state.buildings, state.research, def.requiresBuildings, def.requiresResearch)) return state;
  const targetLevel = lvl(state.research, research) + pendingSameKind(state.labQueue, "research", research) + 1;
  const cost = F.researchCost(research as ResearchId, targetLevel);
  if (!canAfford(state.resources, cost)) return state;
  const startTick = queueTail(state.labQueue, state.tick);
  const duration = F.researchTimeTicks(cost, lvl(state.buildings, "researchLab"), state.universeSpeed);
  const item: QueueItem = {
    kind: "research", id: research, targetLevel, startedAtTick: startTick, completesAtTick: startTick + duration,
  };
  return { ...state, resources: debit(state.resources, cost), labQueue: [...state.labQueue, item] };
}

function enqueueUnit(state: GameState, kind: "ship" | "defense", id: string, count: number): GameState {
  const isShip = kind === "ship";
  if (isShip ? !(id in SHIPS) : !(id in DEFENSES)) return state;
  const def = isShip ? SHIPS[id as ShipId] : DEFENSES[id as DefenseId];
  if (!requirementsMet(state.buildings, state.research, def.requiresBuildings, def.requiresResearch)) return state;

  if (!isShip) {
    const maxCount = DEFENSES[id as DefenseId].maxCount;
    if (maxCount !== undefined) {
      const owned = lvl(state.defense, id) + pendingUnitCount(state.shipyardQueue, id);
      if (owned + count > maxCount) return state;
    }
  }

  const unitCost = isShip ? F.shipCost(id as ShipId) : F.defenseCost(id as DefenseId);
  const totalCost = scaleCostBy(unitCost, count);
  if (!canAfford(state.resources, totalCost)) return state;

  const startTick = queueTail(state.shipyardQueue, state.tick);
  const perUnit = F.shipyardTimeTicks(
    unitCost, lvl(state.buildings, "shipyard"), lvl(state.buildings, "naniteFactory"), state.universeSpeed);
  const item: QueueItem = {
    kind, id, count, startedAtTick: startTick, completesAtTick: startTick + perUnit * count,
  };
  return { ...state, resources: debit(state.resources, totalCost), shipyardQueue: [...state.shipyardQueue, item] };
}

// applyCommand — applique un geste joueur VALIDÉ. Mal formé (schéma) ou infaisable → état inchangé, même
// référence (le serveur dispose). Réducteur total : ne lève jamais.
export function applyCommand(state: GameState, command: Command): GameState {
  const parsed = Command.safeParse(command);
  if (!parsed.success) return state;
  const cmd = parsed.data;
  switch (cmd.kind) {
    case "enqueueBuilding":
      return enqueueBuilding(state, cmd.building);
    case "enqueueResearch":
      return enqueueResearch(state, cmd.research);
    case "enqueueShip":
      return enqueueUnit(state, "ship", cmd.ship, cmd.count);
    case "enqueueDefense":
      return enqueueUnit(state, "defense", cmd.defense, cmd.count);
  }
}
