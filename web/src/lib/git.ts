import type { GitAheadBehind, GitReconcile, GitReconcileAction, GitSync, GitSyncState } from './schemas'

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

/** Une ligne du plan de réconciliation dérivé de l'état de sync (source unique = `state` par branche). */
export interface ReconcilePlanItem {
  branch: string
  state: GitSyncState
  /** Ce que la réconciliation ferait pour cette branche (preview, avant exécution). */
  label: string
  /** Vrai ssi ff-only applicable (miroir ou SoT en avance) ; faux = à jour ou bloqué (manuel). */
  actionable: boolean
}

/** Plan de réconciliation **dérivé de l'état de sync** (preview AVANT le POST — jamais un dry-run réseau ;
 *  la source unique backend est l'`state` par branche du GET `/git/sync`). Par branche : miroir en avance → ff
 *  local ; SoT en avance → push miroir ; vraie divergence → bloqué (manuel) ; alignée → rien. Pur. */
export function reconcilePlan(data: GitSync): ReconcilePlanItem[] {
  return Object.entries(data.branches).map(([branch, s]) => {
    switch (s.state) {
      case 'remote_ahead':
        return { branch, state: s.state, label: `ff depuis GitHub (+${s.behind})`, actionable: true }
      case 'local_ahead':
        return { branch, state: s.state, label: `pousser vers GitHub (+${s.ahead})`, actionable: true }
      case 'diverged':
        return { branch, state: s.state, label: 'divergé — réconciliation manuelle', actionable: false }
      default:
        return { branch, state: s.state, label: 'à jour', actionable: false }
    }
  })
}

/** Vrai ssi au moins une branche est réconciliable **ff-only** (miroir ou SoT en avance) → le bouton
 *  « Réconcilier » exécute quelque chose. Tout-`synced` / `diverged` / dégradé n'a rien à ff. Pur. */
export function isReconcilable(data: GitSync): boolean {
  return reconcilePlan(data).some((p) => p.actionable)
}

/** Vrai ssi l'état mérite d'exposer le bouton « Réconcilier » : une divergence réelle (ff-able OU divergée,
 *  pour EXPLIQUER le blocage manuel). Alignée / dégradée (`no_mirror`/`unreachable`) → rien à proposer. Pur. */
export function needsReconcile(data: GitSync): boolean {
  return data.state === 'remote_ahead' || data.state === 'local_ahead' || data.state === 'diverged'
}

const RECONCILE_LABELS: Record<GitReconcileAction, string> = {
  already_synced: 'déjà à jour',
  fast_forward: 'rattrapé (ff)',
  pushed: 'poussé vers GitHub',
  push_failed: 'push échoué',
  blocked_worktree: 'bloqué (worktree actif)',
  blocked_diverged: 'bloqué (divergé)',
}

/** Label lisible de l'action RÉELLEMENT appliquée à une branche (résultat du POST reconcile). Pur. */
export function reconcileActionLabel(action: GitReconcileAction): string {
  return RECONCILE_LABELS[action]
}

/** Âge relatif FR compact (« il y a 3 j ») d'une date ISO — pour dater la liste de fichiers et l'historique
 *  façon GitHub. `now` injectable = testable déterministe. Date non parsable → l'ISO brut (jamais un faux
 *  « à l'instant »). Seuils au plancher (`floor`) : « il y a 3 j » = au moins 3 jours écoulés. Pur. */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return iso
  const sec = Math.max(0, Math.floor((now - t) / 1000))
  if (sec < 60) return "à l'instant"
  const min = Math.floor(sec / 60)
  if (min < 60) return `il y a ${min} min`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `il y a ${hr} h`
  const day = Math.floor(hr / 24)
  if (day < 30) return `il y a ${day} j`
  const mon = Math.floor(day / 30)
  if (mon < 12) return `il y a ${mon} mois`
  const yr = Math.floor(day / 365)
  return `il y a ${yr} an${yr > 1 ? 's' : ''}`
}

/** Score « subsequence » d'un chemin vs une requête (insensible à la casse) : les caractères de la requête
 *  doivent apparaître DANS L'ORDRE dans le texte. Plus petit = meilleur (0 = caractères contigus). `null` si
 *  pas de match. Pénalise les trous entre caractères → un match compact remonte. Requête vide → 0 (tout matche). */
function fuzzyScore(text: string, query: string): number | null {
  const t = text.toLowerCase()
  const q = query.toLowerCase()
  let from = 0
  let score = 0
  let prev = -1
  for (const ch of q) {
    const idx = t.indexOf(ch, from)
    if (idx === -1) return null
    if (prev !== -1) score += idx - prev - 1   // trou entre deux caractères consécutifs (0 si contigus)
    prev = idx
    from = idx + 1
  }
  return score
}

/** Filtre fuzzy « go to file » : garde les chemins qui matchent la requête en subsequence, triés par
 *  compacité du match puis par longueur puis alpha (déterministe). Requête vide → tous (bornés). `limit`
 *  borne l'affichage — la palette n'a pas à peindre des milliers de lignes. Pur, sans dépendance. */
export function fuzzyFilter(paths: string[], query: string, limit = 50): string[] {
  const scored: { path: string; score: number }[] = []
  for (const path of paths) {
    const score = fuzzyScore(path, query)
    if (score !== null) scored.push({ path, score })
  }
  scored.sort((a, b) => a.score - b.score || a.path.length - b.path.length || a.path.localeCompare(b.path))
  return scored.slice(0, limit).map((s) => s.path)
}

/** Résumé lisible du résultat d'une réconciliation (post-POST) : ce qui a bougé, ce qui reste bloqué. Pur. */
export function reconcileOutcome(rep: GitReconcile): string {
  if (!rep.fetched) return rep.state === 'no_mirror' ? 'pas de miroir' : 'miroir injoignable'
  if (rep.blocked.length > 0) {
    return `${rep.blocked.length} branche(s) bloquée(s) — réconciliation manuelle requise`
  }
  const moved = Object.values(rep.actions).filter((a) => a.action === 'fast_forward' || a.action === 'pushed')
  return moved.length > 0 ? `${moved.length} branche(s) réconciliée(s)` : 'déjà synchronisé'
}
