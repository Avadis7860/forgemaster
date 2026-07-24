// rng.test.ts — le RNG counter-based adressable est DÉTERMINISTE (rejeu) et décorrélé par domaine/ids.
import { describe, expect, it } from "vitest";

import { rngChance, rngFloat, rngInt, rngUint32 } from "./rng.js";

describe("RNG counter-based adressable", () => {
  it("déterministe : mêmes coordonnées → même valeur", () => {
    expect(rngUint32(1, "battle", 3, 4)).toBe(rngUint32(1, "battle", 3, 4));
    expect(rngFloat(1, "battle", 3, 4)).toBe(rngFloat(1, "battle", 3, 4));
  });

  it("décorrélé : domaine / ids / seed différents → valeur différente", () => {
    expect(rngUint32(1, "battle", 3)).not.toBe(rngUint32(1, "universe", 3));
    expect(rngUint32(1, "battle", 3)).not.toBe(rngUint32(1, "battle", 4));
    expect(rngUint32(1, "battle", 3)).not.toBe(rngUint32(2, "battle", 3));
  });

  it("rngInt reste dans [0, bound)", () => {
    for (let i = 0; i < 50; i++) {
      const v = rngInt(7, "target", 6, i);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(6);
    }
  });

  it("rngChance déterministe et borné (p=0 → jamais, p=1 → toujours)", () => {
    expect(rngChance(1, "explosion", 0.5, 2)).toBe(rngChance(1, "explosion", 0.5, 2));
    expect(rngChance(1, "explosion", 0, 2)).toBe(false);
    expect(rngChance(1, "explosion", 1, 2)).toBe(true);
  });
});
