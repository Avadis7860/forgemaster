// formulas.test.ts — tests-ORACLE des formules ogame sourcées (valeurs de référence Fandom/compendium). Ce
// sont les garde-fous d'exactitude : si une constante dérive, un oracle rougit.
import { describe, expect, it } from "vitest";

import * as F from "./formulas.js";

describe("formules ogame sourcées (oracle)", () => {
  it("coût de la mine de métal : L1 = base, L2 = ×1.5 floored", () => {
    expect(F.buildingCost("metalMine", 1)).toEqual({ metal: 60, crystal: 15, deuterium: 0 });
    expect(F.buildingCost("metalMine", 2)).toEqual({ metal: 90, crystal: 22, deuterium: 0 });
  });

  it("production de la mine de métal : L1 = 33/h (30·1·1.1)", () => {
    expect(F.mineProduction("metalMine", 1, { universeSpeed: 1, temperature: 20 })).toBe(33);
  });

  it("production de deutérium dépend de la température (10·1·1.1·1.24 = 13)", () => {
    expect(F.mineProduction("deuteriumSynthesizer", 1, { universeSpeed: 1, temperature: 20 })).toBe(13);
  });

  it("énergie : conso mine (11) et prod solaire (22) = base·1·1.1", () => {
    expect(F.energyConsumption("metalMine", 1)).toBe(11);
    expect(F.energyProduction("solarPlant", 1)).toBe(22);
  });

  it("capacité de stockage : niveau 0 = 10000, niveau 1 = 20000", () => {
    expect(F.storageCapacity(0)).toBe(10000);
    expect(F.storageCapacity(1)).toBe(20000);
  });

  it("coque dérivée du coût ((metal+crystal)/10) — chasseur léger = 400", () => {
    expect(F.hull({ metal: 3000, crystal: 1000, deuterium: 0 })).toBe(400);
  });

  it("temps de construction : /2500, réduit par l'usine de robots, min 1 tick", () => {
    const cost = { metal: 100000, crystal: 0, deuterium: 0 };
    expect(F.buildTimeTicks(cost, 0, 0, 1)).toBe(40); // 100000/2500
    expect(F.buildTimeTicks(cost, 9, 0, 1)).toBe(4); // /(1+9)
    expect(F.buildTimeTicks({ metal: 10, crystal: 0, deuterium: 0 }, 0, 0, 1)).toBe(1); // plancher 1
  });

  it("temps de recherche : /1000, réduit par le laboratoire", () => {
    const cost = { metal: 5000, crystal: 5000, deuterium: 0 };
    expect(F.researchTimeTicks(cost, 0, 1)).toBe(10); // 10000/1000
    expect(F.researchTimeTicks(cost, 4, 1)).toBe(2); // /(1+4)
  });
});
