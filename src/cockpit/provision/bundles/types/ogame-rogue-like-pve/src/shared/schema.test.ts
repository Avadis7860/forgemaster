// schema.test.ts — test PUR du modèle de domaine partagé (Vitest, aucune I/O). Prouve que le gate
// `tsc --noEmit && vitest run` est vert dès l'amorçage et que l'état/les commandes valident.
import { describe, expect, it } from "vitest";

import { Command, GameState, Resources } from "./schema.js";
import { initialGameState } from "./tick.js";

describe("modèle de domaine partagé", () => {
  it("valide un état initial (500/500, tick 0)", () => {
    const s = GameState.parse(initialGameState({ runSeed: 7 }));
    expect(s.resources.metal).toBe(500);
    expect(s.resources.crystal).toBe(500);
    expect(s.tick).toBe(0);
  });

  it("rejette une ressource négative (invariant serveur-autoritatif)", () => {
    expect(() => Resources.parse({ metal: -1, crystal: 0, deuterium: 0 })).toThrow();
  });

  it("valide une commande d'enfilement de bâtiment", () => {
    const cmd = Command.parse({ kind: "enqueueBuilding", building: "metalMine" });
    expect(cmd.kind).toBe("enqueueBuilding");
  });

  it("rejette une commande de kind inconnu", () => {
    expect(() => Command.parse({ kind: "bogus" })).toThrow();
  });
});
