// combat.test.ts — tests du résolveur de bataille. Trois scénarios ORACLE au résultat **indépendant du seed**
// (force écrasante / débris de vaisseaux / rebond), plus déterminisme (rejeu byte-identique), seed-matters,
// rapidfire (prouvé par comparaison), explosion (prouvée probabiliste) et garde `MAX_UNITS`.
import { describe, expect, it } from "vitest";

import { resolveBattle, effectiveStats, MAX_UNITS, type Force } from "./combat.js";
import { SHIPS } from "./data/ships.js";

const F = (units: Record<string, number>, research: Record<string, number> = {}): Force => ({ units, research });
const ctx = (runSeed: number, battleId = 1) => ({ runSeed, battleId });

describe("effectiveStats — bonus de tech +10 %/niveau (floored)", () => {
  it("arme ×(1+0.1·weapons), bouclier ×shielding, coque ×armour", () => {
    expect(effectiveStats(SHIPS.lightFighter, {})).toEqual({ weapon: 50, shieldMax: 10, hullMax: 400 });
    expect(effectiveStats(SHIPS.lightFighter, { weaponsTech: 2 }).weapon).toBe(60); // 50·1.2
    expect(effectiveStats(SHIPS.lightFighter, { armourTech: 3 }).hullMax).toBe(520); // 400·1.3
    expect(effectiveStats(SHIPS.battleship, { shieldingTech: 5 }).shieldMax).toBe(300); // 200·1.5
  });
});

describe("resolveBattle — oracles indépendants du seed", () => {
  it("force écrasante : 5 vaisseaux de bataille éradiquent 1 lanceur, aucune perte", () => {
    const r = resolveBattle(F({ battleship: 5 }), F({ rocketLauncher: 1 }), ctx(7));
    expect(r.winner).toBe("attacker");
    expect(r.attackerLosses).toEqual({});
    expect(r.defenderLosses).toEqual({ rocketLauncher: 1 });
    expect(r.attackerSurvivors).toEqual({ battleship: 5 });
    expect(r.debris).toEqual({ metal: 0, crystal: 0 }); // la défense ne fait PAS de débris
    expect(r.moonChance).toBe(0);
    expect(r.plunderCapacity).toBe(7500); // 5 · cargo(1500)
    // réparation défense : déterministe, borné (0 ou 1 lanceur réparé)
    expect(Object.keys(r.defenseRepair).every((k) => k === "rocketLauncher")).toBe(true);
    expect(r.defenseRepair.rocketLauncher ?? 0).toBeLessThanOrEqual(1);
  });

  it("débris de vaisseaux : 1 chasseur léger détruit ⇒ 30 % de (3000,1000) = (900,300)", () => {
    const r = resolveBattle(F({ battleship: 3 }), F({ lightFighter: 1 }), ctx(3));
    expect(r.winner).toBe("attacker");
    expect(r.defenderLosses).toEqual({ lightFighter: 1 });
    expect(r.debris).toEqual({ metal: 900, crystal: 300 });
    expect(r.moonChance).toBe(0);
  });

  it("rebond : recycleur (arme 1) vs grand bouclier (10000) — 0 dégât des deux côtés, nul en 6 rounds", () => {
    const r = resolveBattle(F({ recycler: 1 }), F({ largeShieldDome: 1 }), ctx(42));
    expect(r.rounds).toBe(6);
    expect(r.winner).toBe("draw");
    expect(r.attackerLosses).toEqual({});
    expect(r.defenderLosses).toEqual({});
    expect(r.attackerSurvivors).toEqual({ recycler: 1 });
    expect(r.defenderSurvivors).toEqual({ largeShieldDome: 1 });
    expect(r.debris).toEqual({ metal: 0, crystal: 0 });
  });
});

describe("resolveBattle — déterminisme", () => {
  it("même (forces, seed, battleId) ⇒ rapport byte-identique (rejeu)", () => {
    const a = F({ lightFighter: 20, cruiser: 3 });
    const d = F({ rocketLauncher: 30, lightLaser: 10 }, { weaponsTech: 2 });
    expect(resolveBattle(a, d, ctx(123))).toEqual(resolveBattle(a, d, ctx(123)));
  });

  it("seed différent ⇒ trajectoire différente (combat serré symétrique)", () => {
    const a = F({ lightFighter: 20 });
    const d = F({ lightFighter: 20 });
    expect(resolveBattle(a, d, ctx(1))).not.toEqual(resolveBattle(a, d, ctx(999)));
  });
});

describe("resolveBattle — mécaniques sourcées", () => {
  it("rapidfire : le croiseur (RF 6 vs chasseur léger) peut tuer plusieurs cibles dans son unique round létal", () => {
    // Les deux tireurs meurent au round 1 (assez de chasseurs) ⇒ leurs kills = kills du round 1. Sans rapidfire un
    // tireur ne tire qu'UNE fois/round ⇒ TOUJOURS ≤ 1 kill. Le croiseur, lui, dépasse 1 (chaîne de rapidfire) —
    // impossible sans RF. Balayage de seeds : le vaisseau reste bloqué à 1, le croiseur monte au-dessus.
    let maxCruiser = 0;
    for (let seed = 1; seed <= 20; seed++) {
      const cruiserKills = resolveBattle(F({ cruiser: 1 }), F({ lightFighter: 60 }), ctx(seed)).defenderLosses.lightFighter ?? 0;
      const shipKills = resolveBattle(F({ battleship: 1 }), F({ lightFighter: 130 }), ctx(seed)).defenderLosses.lightFighter ?? 0;
      expect(shipKills).toBe(1); // un tir/round, une cible : jamais > 1 (pas de RF du vaisseau vs chasseur)
      maxCruiser = Math.max(maxCruiser, cruiserKills);
    }
    expect(maxCruiser).toBeGreaterThanOrEqual(2); // le RF autorise plusieurs tirs dans le round ⇒ > 1 kill
  });

  it("explosion : un tir de croiseur (400) ne peut PAS tuer un chasseur lourd (coque 1000) sauf par explosion", () => {
    // Croiseur (1 tir, pas de RF vs chasseur lourd) meurt round 1 : sa cible passe à 625 de coque (>0) — seule
    // l'explosion (<70 %, proba 1−coque/coque₀) peut la tuer. Sur un balayage de seeds, on doit voir 0 ET 1.
    const kills = new Set<number>();
    for (let seed = 1; seed <= 40; seed++) {
      kills.add(resolveBattle(F({ cruiser: 1 }), F({ heavyFighter: 20 }), ctx(seed)).defenderLosses.heavyFighter ?? 0);
    }
    expect(kills.has(0)).toBe(true); // parfois l'explosion ne se déclenche pas
    expect(kills.has(1)).toBe(true); // parfois si ⇒ l'explosion tue (impossible par les dégâts seuls)
    expect([...kills].every((k) => k <= 1)).toBe(true);
  });

  it("garde MAX_UNITS : au-delà, on lève (pas de troncature muette)", () => {
    expect(() => resolveBattle(F({ lightFighter: MAX_UNITS + 1 }), F({ lightFighter: 1 }), ctx(1))).toThrow(/MAX_UNITS/);
  });
});
