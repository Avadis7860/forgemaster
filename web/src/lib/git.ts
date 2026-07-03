import type { GitAheadBehind } from './schemas'

/** Vrai ssi les logs `dev`/`main` doivent être fusionnés en UN : les deux réfs sont alignées (même commit,
 *  logs identiques) ET les deux sont présentes. Pur — évite d'afficher deux colonnes de log redondantes. */
export function isLogUnified(ab: GitAheadBehind | null, refCount: number): boolean {
  return Boolean(ab && ab.ahead === 0 && ab.behind === 0) && refCount > 1
}
