// schema.ts — modèle de domaine Zod **PARTAGÉ** (décision verrouillée 1 : un seul univers TS, schémas partagés
// `web/` ↔ `server/`). Source unique de vérité de l'état de jeu ogame. Le serveur reste seul maître de sa
// mutation (décision 2) ; le client lit + propose des commandes. Les catalogues (bâtiments/recherches/flotte/
// défense) vivent dans `./data/*` ; ici : la FORME de l'état + les commandes joueur.
import { z } from "zod";

// Ressources dépensables (l'énergie n'est PAS stockée : elle est un bilan par tick qui scale la production).
export const Resources = z.object({
  metal: z.number().int().nonnegative(),
  crystal: z.number().int().nonnegative(),
  deuterium: z.number().int().nonnegative(),
});
export type Resources = z.infer<typeof Resources>;

export const Coordinates = z.object({
  galaxy: z.number().int().min(1),
  system: z.number().int().min(1),
  position: z.number().int().min(1),
});
export type Coordinates = z.infer<typeof Coordinates>;

export const Planet = z.object({
  coords: Coordinates,
  temperature: z.number().int(), // température max — pilote la production de deutérium
});
export type Planet = z.infer<typeof Planet>;

// Cartes de niveaux/quantités : clé = id (bâtiment/recherche/vaisseau/défense), valeur = niveau ou nombre.
// Record souple (string → entier ≥ 0) : le code lit via des ids typés (`lvl(map, id)`), Zod garantit le domaine
// des valeurs. Une clé absente vaut 0.
export const CountMap = z.record(z.string(), z.number().int().nonnegative());
export type CountMap = z.infer<typeof CountMap>;

// File de construction (bâtiments / recherche / chantier). Ressources débitées à l'enfilement (modèle OGame) ;
// la tête est « active » avec son `completesAtTick`. `targetLevel` pour bâtiment/recherche, `count` pour
// vaisseau/défense.
export const QueueKind = z.enum(["building", "research", "ship", "defense"]);
export type QueueKind = z.infer<typeof QueueKind>;

export const QueueItem = z.object({
  kind: QueueKind,
  id: z.string(),
  targetLevel: z.number().int().positive().optional(),
  count: z.number().int().positive().optional(),
  startedAtTick: z.number().int().nonnegative(),
  completesAtTick: z.number().int().positive(),
});
export type QueueItem = z.infer<typeof QueueItem>;

// GameState — état canonique, avancé UNIQUEMENT par le serveur (décision 2). Déterministe par `runSeed`
// (décision 4/5). `universeSpeed` compresse le temps (twist roguelike : un run se joue en 30–90 min).
export const GameState = z.object({
  tick: z.number().int().nonnegative(),
  universeSpeed: z.number().int().positive(),
  runSeed: z.number().int().nonnegative(),
  planet: Planet,
  resources: Resources,
  buildings: CountMap,
  research: CountMap,
  fleet: CountMap,
  defense: CountMap,
  construction: z.array(QueueItem), // file bâtiments (usine de robots/nanites accélèrent)
  labQueue: z.array(QueueItem), // file recherche (laboratoire accélère)
  shipyardQueue: z.array(QueueItem), // file chantier (vaisseaux + défense, séquentielle)
});
export type GameState = z.infer<typeof GameState>;

// Command — geste joueur PROPOSÉ par le client, VALIDÉ + appliqué par le serveur (anti-triche). Union
// discriminée : enfiler un bâtiment / une recherche / une série de vaisseaux / une série de défenses.
export const Command = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("enqueueBuilding"), building: z.string() }),
  z.object({ kind: z.literal("enqueueResearch"), research: z.string() }),
  z.object({ kind: z.literal("enqueueShip"), ship: z.string(), count: z.number().int().positive() }),
  z.object({ kind: z.literal("enqueueDefense"), defense: z.string(), count: z.number().int().positive() }),
]);
export type Command = z.infer<typeof Command>;
