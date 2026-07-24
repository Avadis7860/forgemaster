// combat.ts — résolveur de bataille DÉTERMINISTE (spec §Combat, sourcé Fandom « Combat » + moteur OPBE). Couche
// domaine **PURE** au même titre que `formulas.ts`/`tick.ts` : `resolveBattle` calcule un `BattleReport` (pertes,
// débris, lune, pillage, réparation) et **ne mute AUCUN `GameState`** — l'APPLICATION du rapport à un univers
// (débit, fret, champ de débris, lune) est câblée en F3. Le mouvement/dispatch qui amène deux flottes au même
// endroit est aussi F3 : ici, les deux forces sont déjà en présence.
//
// Fidélité OGame : ≤ 6 rounds ; à chaque round les boucliers sont **régénérés** au max ; TOUTES les unités
// vivantes en début de round tirent (feu simultané — une unité détruite ce round tire quand même), sur une cible
// tirée dans le **roster de début de round** (d'où l'overkill possible) ; **bounce** si `arme < 1 % du bouclier
// max` ; le bouclier courant absorbe puis la coque ; **rapidfire** relance un tir (nouvelle cible) avec proba
// `(r−1)/r` ; **explosion** d'une unité sous 70 % de coque avec proba `1 − coque/coque₀` par coup encaissé. Seul
// aléa : cible / rapidfire / explosion / réparation — tout via `rng.ts` **adressable** battle-scoped → rejeu
// byte-identique. Aucune horloge murale.
import { SHIPS, SHIP_IDS, type ShipId, type CombatUnitDef } from "./data/ships.js";
import { DEFENSES, DEFENSE_IDS, type DefenseId } from "./data/defense.js";
import { hull } from "./formulas.js";
import { rngInt, rngChance } from "./rng.js";
import type { CountMap } from "./schema.js";

const MAX_ROUNDS = 6;
const BOUNCE_FRACTION = 0.01; // arme < 1 % du bouclier max ⇒ le tir rebondit (0 dégât)
const EXPLOSION_THRESHOLD = 0.7; // coque < 70 % ⇒ risque d'explosion
const DEBRIS_RATIO = 0.3; // 30 % du (métal+cristal) des VAISSEAUX détruits → champ de débris
const DEFENSE_REPAIR = 0.7; // ~70 % des défenses détruites sont réparées (par unité)
const MOON_DEBRIS_UNIT = 100000; // 1 % de chance de lune par tranche de 100k de débris, plafond 20 %
const MOON_CHANCE_CAP = 20;

// Garde-fou anti-explosion combinatoire : au-delà, on lève (pas de troncature muette). Les flottes early-game
// d'un run roguelike sont bien en-deçà ; un dépassement signale un état aberrant (le crash-test veut le voir).
export const MAX_UNITS = 50000;

// Une force au combat : les unités (vaisseaux et/ou défenses) + le niveau de recherche du camp (bonus de combat).
export type Force = { units: CountMap; research: CountMap };

export type ResourceDebris = { metal: number; crystal: number };

export type BattleReport = {
  rounds: number;
  winner: "attacker" | "defender" | "draw";
  attackerLosses: CountMap;
  defenderLosses: CountMap;
  attackerSurvivors: CountMap;
  defenderSurvivors: CountMap;
  debris: ResourceDebris; // vaisseaux détruits (des DEUX camps), 30 % métal/cristal
  moonChance: number; // pourcentage 0..20
  defenseRepair: CountMap; // défenses du défenseur réparées (à appliquer en F3)
  plunderCapacity: number; // capacité de fret des survivants attaquant (pillage ≤ 50 % appliqué en F3)
};

export type BattleContext = { runSeed: number; battleId: number };

// Stats effectives après techs de combat (+10 %/niveau multiplicatif, floored). Point de consommation du champ
// `combatBonus` de `research.ts` (weaponsTech/shieldingTech/armourTech).
export function effectiveStats(
  def: CombatUnitDef, research: CountMap,
): { weapon: number; shieldMax: number; hullMax: number } {
  const w = 1 + 0.1 * (research["weaponsTech"] ?? 0);
  const s = 1 + 0.1 * (research["shieldingTech"] ?? 0);
  const a = 1 + 0.1 * (research["armourTech"] ?? 0);
  return {
    weapon: Math.floor(def.weapon * w),
    shieldMax: Math.floor(def.shield * s),
    hullMax: Math.floor(hull(def.baseCost) * a),
  };
}

// Une entité de combat individuelle (une unité expansée depuis les compteurs). Coque/bouclier mutés en place
// pendant la résolution ; l'entrée `Force` n'est jamais mutée (entités fraîches).
type Entity = {
  id: string;
  isShip: boolean;
  hull: number;
  hullMax: number;
  shield: number;
  shieldMax: number;
  weapon: number;
  cargo: number;
  rapidfire: Partial<Record<string, number>>;
};

function unitDef(id: string): CombatUnitDef | undefined {
  if (id in SHIPS) return SHIPS[id as ShipId];
  if (id in DEFENSES) return DEFENSES[id as DefenseId];
  return undefined;
}

// totalUnits — somme des compteurs valides (garde MAX_UNITS AVANT d'allouer les entités).
function totalUnits(force: Force): number {
  let n = 0;
  for (const id of [...SHIP_IDS, ...DEFENSE_IDS]) n += Math.max(0, Math.floor(force.units[id] ?? 0));
  return n;
}

// expandForce — déplie les compteurs en entités par-unité, dans l'ordre STABLE (SHIP_IDS puis DEFENSE_IDS) pour
// un tirage de cible reproductible. Applique les bonus de tech du camp.
function expandForce(force: Force): Entity[] {
  const out: Entity[] = [];
  for (const id of [...SHIP_IDS, ...DEFENSE_IDS]) {
    const count = Math.max(0, Math.floor(force.units[id] ?? 0));
    if (count === 0) continue;
    const def = unitDef(id);
    if (def === undefined) continue;
    const isShip = id in SHIPS;
    const st = effectiveStats(def, force.research);
    for (let k = 0; k < count; k++) {
      out.push({
        id, isShip, hull: st.hullMax, hullMax: st.hullMax, shield: st.shieldMax, shieldMax: st.shieldMax,
        weapon: st.weapon, cargo: def.cargo, rapidfire: def.rapidfire,
      });
    }
  }
  return out;
}

function countById(entities: readonly Entity[]): CountMap {
  const m: CountMap = {};
  for (const e of entities) m[e.id] = (m[e.id] ?? 0) + 1;
  return m;
}

function diffCounts(initial: CountMap, survivors: CountMap): CountMap {
  const m: CountMap = {};
  for (const [id, n] of Object.entries(initial)) {
    const lost = n - (survivors[id] ?? 0);
    if (lost > 0) m[id] = lost;
  }
  return m;
}

// applyShot — un tir de `shooter` sur `target` (mute `target`). Bounce / absorption bouclier / coque / explosion.
function applyShot(
  shooter: Entity, target: Entity, side: number, round: number, s: number, shot: number, ctx: BattleContext,
): void {
  if (shooter.weapon < BOUNCE_FRACTION * target.shieldMax) return; // rebond : 0 dégât
  let dmg = shooter.weapon;
  if (target.shield >= dmg) {
    target.shield -= dmg;
    return;
  }
  dmg -= target.shield;
  target.shield = 0;
  target.hull -= dmg;
  if (target.hull > 0 && target.hull < EXPLOSION_THRESHOLD * target.hullMax) {
    const p = 1 - target.hull / target.hullMax;
    if (rngChance(ctx.runSeed, "explode", p, ctx.battleId, round, side, s, shot)) target.hull = 0;
  }
}

// fireSide — feu d'un camp (roster de début de round `shooters`) sur `targets` (roster de début de round de
// l'adversaire, taille fixe → overkill possible). Chaque tireur : 1 tir + chaîne de rapidfire.
function fireSide(
  shooters: readonly Entity[], targets: readonly Entity[], side: number, round: number, ctx: BattleContext,
): void {
  const nT = targets.length;
  if (nT === 0) return;
  for (let s = 0; s < shooters.length; s++) {
    const shooter = shooters[s];
    if (shooter.weapon <= 0) continue; // sonde : pas d'arme, pas de tir
    let shot = 0;
    for (;;) {
      const target = targets[rngInt(ctx.runSeed, "target", nT, ctx.battleId, round, side, s, shot)];
      applyShot(shooter, target, side, round, s, shot, ctx);
      const r = shooter.rapidfire[target.id] ?? 0;
      shot++;
      if (r > 1 && rngChance(ctx.runSeed, "rf", (r - 1) / r, ctx.battleId, round, side, s, shot)) continue;
      break;
    }
  }
}

// resolveBattle — résout une bataille et rend un `BattleReport` PUR. `ctx.battleId` étiquette la bataille dans le
// flux d'aléa (en F3 = `hash(coords, tick, forces)` ; en test fourni directement). Ne mute pas les entrées.
export function resolveBattle(attacker: Force, defender: Force, ctx: BattleContext): BattleReport {
  if (totalUnits(attacker) + totalUnits(defender) > MAX_UNITS) {
    throw new Error(`resolveBattle : ${totalUnits(attacker) + totalUnits(defender)} unités > MAX_UNITS=${MAX_UNITS}`);
  }
  const attackers = expandForce(attacker);
  const defenders = expandForce(defender);

  let a: Entity[] = attackers;
  let d: Entity[] = defenders;
  let rounds = 0;
  for (let round = 1; round <= MAX_ROUNDS; round++) {
    if (a.length === 0 || d.length === 0) break;
    rounds = round;
    for (const e of a) e.shield = e.shieldMax; // régén début de round
    for (const e of d) e.shield = e.shieldMax;
    fireSide(a, d, 0, round, ctx); // feu simultané : les deux camps visent le roster de début de round
    fireSide(d, a, 1, round, ctx);
    a = a.filter((e) => e.hull > 0);
    d = d.filter((e) => e.hull > 0);
  }

  const attackerSurvivors = countById(a);
  const defenderSurvivors = countById(d);
  const attackerLosses = diffCounts(countById(attackers), attackerSurvivors);
  const defenderLosses = diffCounts(countById(defenders), defenderSurvivors);

  // Débris : 30 % du métal+cristal des VAISSEAUX détruits (des deux camps) — la défense va à la réparation, pas
  // aux débris (règle OGame classique).
  let debrisMetal = 0;
  let debrisCrystal = 0;
  for (const e of [...attackers, ...defenders]) {
    if (e.hull <= 0 && e.isShip) {
      const bc = SHIPS[e.id as ShipId].baseCost;
      debrisMetal += bc.metal;
      debrisCrystal += bc.crystal;
    }
  }
  const debris: ResourceDebris = {
    metal: Math.floor(DEBRIS_RATIO * debrisMetal),
    crystal: Math.floor(DEBRIS_RATIO * debrisCrystal),
  };
  const moonChance = Math.min(Math.floor((debris.metal + debris.crystal) / MOON_DEBRIS_UNIT), MOON_CHANCE_CAP);

  // Réparation défense : par unité détruite du défenseur, ~70 % (tirage adressable → déterministe).
  const defenseRepair: CountMap = {};
  const repairIndex: Record<string, number> = {};
  for (const e of defenders) {
    if (e.isShip || e.hull > 0) continue;
    const k = repairIndex[e.id] ?? 0;
    repairIndex[e.id] = k + 1;
    const defIx = DEFENSE_IDS.indexOf(e.id as DefenseId);
    if (rngChance(ctx.runSeed, "repair", DEFENSE_REPAIR, ctx.battleId, defIx, k)) {
      defenseRepair[e.id] = (defenseRepair[e.id] ?? 0) + 1;
    }
  }

  let plunderCapacity = 0;
  for (const e of a) if (e.isShip) plunderCapacity += e.cargo;

  const winner: BattleReport["winner"] =
    a.length > 0 && d.length === 0 ? "attacker" : d.length > 0 && a.length === 0 ? "defender" : "draw";

  return {
    rounds, winner, attackerLosses, defenderLosses, attackerSurvivors, defenderSurvivors,
    debris, moonChance, defenseRepair, plunderCapacity,
  };
}
