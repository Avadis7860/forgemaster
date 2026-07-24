// rng.ts — RNG **counter-based ADRESSABLE** (décision verrouillée 4 + spec P2). Un flux d'aléa reproductible
// INDEXÉ par ses coordonnées `(runSeed, domaine, ...ids)` — pas d'état mutable filé de fonction en fonction :
// deux appels aux mêmes coordonnées rendent la même valeur, quel que soit l'ordre d'évaluation. Aucune horloge
// murale, entiers 32-bit, math déterministe → rejeu byte-identique (combat, génération d'univers, expéditions).
//
// Usage : `rngInt(runSeed, "battle", 6, battleId, round, unitIx)` tire une cible ; `rngFloat(runSeed,
// "universe", empireId)` place un empire. Le `domain` étiquette un sous-flux décorrélé (deux domaines aux
// mêmes ids ne se corrèlent pas). Le hash final est un avalanche (murmur/splitmix-like) : bonne dispersion.

const FNV_OFFSET = 0x811c9dc5;
const FNV_PRIME = 0x01000193;

// hashDomain — hash stable 32-bit d'une étiquette de sous-flux (FNV-1a sur les code-points). Pur.
function hashDomain(domain: string): number {
  let h = FNV_OFFSET >>> 0;
  for (let i = 0; i < domain.length; i++) {
    h = Math.imul(h ^ domain.charCodeAt(i), FNV_PRIME) >>> 0;
  }
  return h >>> 0;
}

// mix32 — mélange une liste d'entiers en un hash 32-bit avalanché (FNV-1a octet-par-octet + finalizer
// splitmix32). Déterministe, sans état : c'est le cœur adressable.
function mix32(nums: readonly number[]): number {
  let h = FNV_OFFSET >>> 0;
  for (const n of nums) {
    let x = n >>> 0;
    for (let b = 0; b < 4; b++) {
      h = Math.imul(h ^ (x & 0xff), FNV_PRIME) >>> 0;
      x >>>= 8;
    }
  }
  h ^= h >>> 16;
  h = Math.imul(h, 0x7feb352d) >>> 0;
  h ^= h >>> 15;
  h = Math.imul(h, 0x846ca68b) >>> 0;
  h ^= h >>> 16;
  return h >>> 0;
}

// rngUint32 — entier non signé 32-bit reproductible pour la coordonnée `(runSeed, domain, ...ids)`.
export function rngUint32(runSeed: number, domain: string, ...ids: number[]): number {
  return mix32([runSeed >>> 0, hashDomain(domain), ...ids]);
}

// rngFloat — flottant dans [0, 1) reproductible.
export function rngFloat(runSeed: number, domain: string, ...ids: number[]): number {
  return rngUint32(runSeed, domain, ...ids) / 4294967296;
}

// rngInt — entier dans [0, bound) reproductible (bound > 0). `bound` entre dans le hash → deux tirages de
// bornes différentes aux mêmes ids ne sont pas corrélés. Biais modulo négligeable pour nos petites bornes.
export function rngInt(runSeed: number, domain: string, bound: number, ...ids: number[]): number {
  if (bound <= 0) throw new Error("rngInt: bound doit être > 0");
  return mix32([runSeed >>> 0, hashDomain(domain), bound >>> 0, ...ids]) % bound;
}

// rngChance — vrai avec probabilité `p` (0..1), reproductible. Utilisé pour rapidfire / explosion / lune.
export function rngChance(runSeed: number, domain: string, p: number, ...ids: number[]): boolean {
  return rngFloat(runSeed, domain, ...ids) < p;
}
