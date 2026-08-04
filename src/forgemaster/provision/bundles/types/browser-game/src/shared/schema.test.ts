// schema.test.ts — test PUR du modèle de domaine partagé (Vitest, aucune I/O : la résolution se teste en pur
// avant l'UI, décision verrouillée). Il prouve que le gate `tsc --noEmit && vitest run` est vert dès l'amorçage
// et fixe l'invariant « une capacité livrée = un test ». Étends-le au fil du modèle (unités, combat, tick).
import { describe, expect, it } from "vitest";

import { Player, Resource } from "./schema.js";

describe("modèle de domaine partagé", () => {
  it("accepte un joueur d'amorçage valide", () => {
    const player = Player.parse({
      id: "00000000-0000-0000-0000-000000000000",
      name: "seed",
      resources: [{ kind: "credits", amount: 0 }],
    });
    expect(player.resources[0]?.kind).toBe("credits");
  });

  it("rejette une ressource au montant négatif (invariant serveur-autoritatif)", () => {
    expect(() => Resource.parse({ kind: "energy", amount: -1 })).toThrow();
  });
});
