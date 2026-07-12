import type { GitAheadBehind, GitSync } from './schemas'

/** Vrai ssi les logs `dev`/`main` doivent être fusionnés en UN : les deux réfs sont alignées (même commit,
 *  logs identiques) ET les deux sont présentes. Pur — évite d'afficher deux colonnes de log redondantes. */
export function isLogUnified(ab: GitAheadBehind | null, refCount: number): boolean {
  return Boolean(ab && ab.ahead === 0 && ab.behind === 0) && refCount > 1
}

/** Résumé lisible d'un état de sync miroir → label compact. Le compte est injecté sur les états
 *  ACTIONNABLES (`GitHub +N` = SoT en retard, à ff ; `à pousser +N` = SoT en avance). Les dégradations
 *  gardent un label explicite (`injoignable`, `pas de miroir`) — jamais un « à jour » trompeur. Pur. */
export function syncSummary(data: GitSync): string {
  const branches = Object.values(data.branches)
  const behind = branches.reduce((n, s) => n + s.behind, 0)
  const ahead = branches.reduce((n, s) => n + s.ahead, 0)
  switch (data.state) {
    case 'synced': return 'miroir à jour'
    case 'remote_ahead': return `GitHub +${behind}`
    case 'local_ahead': return `à pousser +${ahead}`
    case 'diverged': return 'divergé'
    case 'unreachable': return 'injoignable'
    case 'no_mirror': return 'pas de miroir'
  }
}
