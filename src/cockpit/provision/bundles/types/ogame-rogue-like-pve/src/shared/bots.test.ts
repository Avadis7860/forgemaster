// bots.test.ts — tests des politiques de factions PNJ. Prouve : build-orders déterministes, croissance
// rejeu-identique, archétype-matters (les bots ne consomment PAS d'aléa — le seed compte pour le COMBAT/l'univers,
// pas pour l'économie d'un bot), pureté, et que chaque archétype construit bien sa spécialité.
import { describe, expect, it } from "vitest";

import { botCommands, FACTIONS, type FactionArchetype } from "./bots.js";
import { applyCommand, applyTick, initialGameState } from "./tick.js";
import type { GameState } from "./schema.js";

// step — un tick de bot : applique les commandes candidates (applyCommand est autoritatif : coût + prérequis),
// puis avance la simulation. advance — K ticks depuis un run frais.
function step(state: GameState, faction: FactionArchetype): GameState {
  let s = state;
  for (const cmd of botCommands(s, faction, s.tick)) s = applyCommand(s, cmd);
  return applyTick(s);
}
function advance(faction: FactionArchetype, seed: number, ticks: number): GameState {
  let s = initialGameState({ runSeed: seed });
  for (let i = 0; i < ticks; i++) s = step(s, faction);
  return s;
}

describe("botCommands — politique pure", () => {
  it("chaque archétype propose au moins une commande sur un run frais", () => {
    const s0 = initialGameState({ runSeed: 1 });
    for (const f of FACTIONS) expect(botCommands(s0, f, 0).length).toBeGreaterThan(0);
  });

  it("pureté : botCommands ne mute pas l'état", () => {
    const s0 = initialGameState({ runSeed: 1 });
    const snapshot = JSON.stringify(s0);
    botCommands(s0, "boss", 0);
    expect(JSON.stringify(s0)).toBe(snapshot);
  });
});

describe("bots — croissance déterministe", () => {
  it("rejeu byte-identique : advance(farmer, 100) est reproductible", () => {
    expect(advance("farmer", 1, 100)).toEqual(advance("farmer", 1, 100));
  });

  it("le fermier fait croître son économie (mines + solaire montent)", () => {
    const s = advance("farmer", 1, 100);
    expect(s.buildings.metalMine ?? 0).toBeGreaterThanOrEqual(3);
    expect(s.buildings.solarPlant ?? 0).toBeGreaterThanOrEqual(3);
  });

  it("archétype-matters : farmer et turtle divergent (les bots sont seed-indépendants)", () => {
    expect(advance("farmer", 1, 120)).not.toEqual(advance("turtle", 1, 120));
    // seed-indépendance de l'économie d'un bot : tout est identique SAUF le champ `runSeed` lui-même (les bots
    // ne consomment aucun aléa — le seed compte pour le COMBAT/l'univers, pas pour l'économie).
    const s1 = advance("farmer", 1, 80);
    const s2 = advance("farmer", 2, 80);
    expect({ ...s1, runSeed: 0 }).toEqual({ ...s2, runSeed: 0 });
  });
});

describe("bots — chaque archétype construit sa spécialité", () => {
  it("la tortue érige de la défense", () => {
    const s = advance("turtle", 1, 200);
    expect(s.defense.rocketLauncher ?? 0).toBeGreaterThan(0);
  });

  it("le raider ouvre la voie militaire (chantier spatial)", () => {
    const s = advance("raider", 1, 200);
    expect(s.buildings.shipyard ?? 0).toBeGreaterThanOrEqual(1);
  });
});
