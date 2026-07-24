// schema.ts — modèle de domaine PARTAGÉ (décision verrouillée 1 : un seul univers TS, schémas Zod partagés
// entre `web/` et `server/`). Source unique de vérité du type universe. Amorçage minimal : ressources + joueur ;
// étends-le en É1 (unités, bâtiments, map, bots) — le serveur reste l'autorité (décision 2).
import { z } from "zod";

export const ResourceKind = z.enum(["credits", "matter", "energy"]);
export type ResourceKind = z.infer<typeof ResourceKind>;

export const Resource = z.object({
  kind: ResourceKind,
  amount: z.number().int().nonnegative(),
});
export type Resource = z.infer<typeof Resource>;

export const Player = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  resources: z.array(Resource),
});
export type Player = z.infer<typeof Player>;

// GameState — état canonique du jeu, avancé UNIQUEMENT par le serveur autoritatif (décision 2). Amorçage
// minimal : un compteur de tick + les ressources. Étends-le (unités, bâtiments, map, bots) en gardant le
// serveur seul maître de sa mutation.
export const GameState = z.object({
  tick: z.number().int().nonnegative(),
  resources: z.array(Resource),
});
export type GameState = z.infer<typeof GameState>;

// Command — geste joueur PROPOSÉ par le client, VALIDÉ + appliqué par le serveur (anti-triche : le client
// propose, le serveur dispose). Union discriminée à une variante d'amorçage (dépenser une ressource) —
// étends-la (`build`, `move`, `attack`…) sans jamais déplacer la validation côté client.
export const Command = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("spend"),
    resource: ResourceKind,
    amount: z.number().int().positive(),
  }),
]);
export type Command = z.infer<typeof Command>;
