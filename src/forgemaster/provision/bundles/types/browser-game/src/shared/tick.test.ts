// tick.test.ts — test PUR du cœur de simulation (Vitest, aucune I/O). Rend l'invariant verrouillé
// « même seed + même suite de commandes → même état » EXÉCUTABLE (pas juste déclaratif), et fixe l'invariant
// « une capacité livrée = un test ». Étends-le au fil du modèle (production par bâtiment, combat, IA bots).
import { describe, expect, it } from "vitest";

import { applyCommand, applyTick } from "./tick.js";
import type { GameState } from "./schema.js";

const seedState: GameState = { tick: 0, resources: [{ kind: "credits", amount: 0 }] };

describe("simulation déterministe (contrat verrouillé (état, commandes, seed) → état')", () => {
  it("même seed + même suite de commandes → même état (rejeu byte-identique)", () => {
    const run = (): GameState => {
      let s = seedState;
      for (let i = 0; i < 10; i++) s = applyTick(s, 42);
      return applyCommand(s, { kind: "spend", resource: "credits", amount: 25 });
    };
    expect(run()).toEqual(run());
  });

  it("un seed différent produit une trajectoire différente (le seed compte réellement)", () => {
    // Compare la TRAJECTOIRE complète (le montant à chaque tick), pas l'état final : deux trajectoires
    // 10-ticks identiques exigeraient un jitter égal à CHAQUE tick (~(1/5)^10), pas juste une somme égale.
    const trajectory = (seed: number): number[] => {
      let s = seedState;
      const amounts: number[] = [];
      for (let i = 0; i < 10; i++) {
        s = applyTick(s, seed);
        amounts.push(s.resources[0]?.amount ?? 0);
      }
      return amounts;
    };
    expect(trajectory(1)).not.toEqual(trajectory(999));
  });

  it("un tick avance le compteur et produit des ressources", () => {
    const s = applyTick(seedState, 7);
    expect(s.tick).toBe(1);
    expect(s.resources[0]?.amount).toBeGreaterThan(0);
  });

  it("une dépense valide débite le solde", () => {
    const s: GameState = { tick: 3, resources: [{ kind: "credits", amount: 50 }] };
    const after = applyCommand(s, { kind: "spend", resource: "credits", amount: 20 });
    expect(after.resources[0]?.amount).toBe(30);
  });

  it("une dépense au-delà du solde est refusée (état inchangé — le serveur dispose)", () => {
    const s: GameState = { tick: 3, resources: [{ kind: "credits", amount: 5 }] };
    const after = applyCommand(s, { kind: "spend", resource: "credits", amount: 999 });
    expect(after).toBe(s); // même référence : refus strict, aucune mutation
  });

  it("applyTick ne mute pas l'état d'entrée (pureté)", () => {
    const s: GameState = { tick: 0, resources: [{ kind: "credits", amount: 0 }] };
    applyTick(s, 3);
    expect(s).toEqual({ tick: 0, resources: [{ kind: "credits", amount: 0 }] });
  });
});
