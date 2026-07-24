// tick.test.ts — test PUR du cœur de simulation (Vitest, aucune I/O). Rend EXÉCUTABLES les invariants
// verrouillés : déterminisme (rejeu byte-identique), production ogame, files de construction, refus
// serveur-autoritatif (état inchangé, même référence), pureté.
import { describe, expect, it } from "vitest";

import { applyCommand, applyTick, initialGameState } from "./tick.js";
import type { GameState } from "./schema.js";

const runTicks = (s: GameState, n: number): GameState => {
  let cur = s;
  for (let i = 0; i < n; i++) cur = applyTick(cur);
  return cur;
};

describe("simulation déterministe + économie ogame", () => {
  it("même seed + mêmes commandes → même état (rejeu byte-identique)", () => {
    const run = (): GameState => {
      let s = initialGameState({ runSeed: 42 });
      s = applyCommand(s, { kind: "enqueueBuilding", building: "metalMine" });
      return runTicks(s, 20);
    };
    expect(run()).toEqual(run());
  });

  it("un tick produit du métal/cristal dès L0 (revenu de base)", () => {
    const s = applyTick(initialGameState());
    expect(s.tick).toBe(1);
    expect(s.resources.metal).toBe(530); // 500 + base 30
    expect(s.resources.crystal).toBe(515); // 500 + base 15
  });

  it("enfiler une mine débite le coût et l'ajoute à la file", () => {
    const s = applyCommand(initialGameState(), { kind: "enqueueBuilding", building: "metalMine" });
    expect(s.resources.metal).toBe(440); // 500 - 60
    expect(s.resources.crystal).toBe(485); // 500 - 15
    expect(s.construction).toHaveLength(1);
    expect(s.construction[0].targetLevel).toBe(1);
  });

  it("la mine se termine et monte de niveau (file drainée)", () => {
    let s = applyCommand(initialGameState(), { kind: "enqueueBuilding", building: "metalMine" });
    s = runTicks(s, s.construction[0].completesAtTick);
    expect(s.buildings.metalMine).toBe(1);
    expect(s.construction).toHaveLength(0);
  });

  it("refus si ressources insuffisantes → état inchangé (même référence)", () => {
    const empty: GameState = { ...initialGameState(), resources: { metal: 0, crystal: 0, deuterium: 0 } };
    const after = applyCommand(empty, { kind: "enqueueBuilding", building: "metalMine" });
    expect(after).toBe(empty);
  });

  it("refus si prérequis manquant (chantier requiert usine de robots 2) → même référence", () => {
    const s = initialGameState();
    const after = applyCommand(s, { kind: "enqueueBuilding", building: "shipyard" });
    expect(after).toBe(s);
  });

  it("une commande différente produit une trajectoire différente", () => {
    const base = runTicks(initialGameState(), 10);
    let other = applyCommand(initialGameState(), { kind: "enqueueBuilding", building: "metalMine" });
    other = runTicks(other, 10);
    expect(other).not.toEqual(base);
  });

  it("applyTick ne mute pas l'état d'entrée (pureté)", () => {
    const s = initialGameState();
    const snapshot = JSON.stringify(s);
    applyTick(s);
    expect(JSON.stringify(s)).toBe(snapshot);
  });
});
