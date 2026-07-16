// schema.ts — modèle de domaine PARTAGÉ (décision verrouillée 1 : un seul univers TS, schémas Zod partagés
// entre `web/` et `server/`). Source unique de vérité du type universe. Amorçage minimal : ressources + joueur ;
// étends-le en É1 (unités, bâtiments, map, bots) — le serveur reste l'autorité (décision 2).
import { z } from "zod";

export const Resource = z.object({
  kind: z.enum(["credits", "matter", "energy"]),
  amount: z.number().int().nonnegative(),
});
export type Resource = z.infer<typeof Resource>;

export const Player = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  resources: z.array(Resource),
});
export type Player = z.infer<typeof Player>;
