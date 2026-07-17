// statusTone — SOURCE UNIQUE des teintes sémantiques de statut (doctrine : pas de
// bg-*-100/text-*-800 inline dispersé). Les classes sont LITTÉRALES : Tailwind v4
// scanne le texte source, une classe construite dynamiquement ne serait pas générée.

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'purple' | 'accent' | 'neutral'

const TONE_BADGE: Record<Tone, string> = {
  ok: 'bg-ok-500/15 text-ok-500 border-ok-500/30',
  warn: 'bg-warn-500/15 text-warn-500 border-warn-500/30',
  danger: 'bg-danger-500/15 text-danger-500 border-danger-500/30',
  info: 'bg-info-500/15 text-info-500 border-info-500/30',
  purple: 'bg-purple-500/15 text-purple-500 border-purple-500/30',
  accent: 'bg-accent-500/15 text-accent-400 border-accent-500/30',
  neutral: 'bg-faint/10 text-muted border-border',
}

const TONE_DOT: Record<Tone, string> = {
  ok: 'bg-ok-500',
  warn: 'bg-warn-500',
  danger: 'bg-danger-500',
  info: 'bg-info-500',
  purple: 'bg-purple-500',
  accent: 'bg-accent-500',
  neutral: 'bg-faint',
}

/** État de task classé par le résolveur (resolver.classify). */
export const TASK_STATE_TONE: Record<string, Tone> = {
  READY: 'accent',
  ACTIVE: 'info',
  DONE: 'ok',
  BLOCKED_DEPS: 'warn',
  BLOCKED: 'warn',
  CYCLE: 'danger',
  ERROR: 'danger',
  CANCELLED: 'neutral',
}

/** Status brut d'une task (colonne DB). */
export const TASK_STATUS_TONE: Record<string, Tone> = {
  todo: 'neutral',
  in_progress: 'info',
  done: 'ok',
  blocked: 'warn',
  cancelled: 'neutral',
}

/** Status d'une feature (colonne DB). */
export const FEATURE_STATUS_TONE: Record<string, Tone> = {
  planned: 'neutral',
  active: 'info',
  ready: 'accent',
  merged: 'ok',
  cancelled: 'neutral',
}

/** Statut d'un job de dispatch (colonne dispatch_jobs). */
export const JOB_STATUS_TONE: Record<string, Tone> = {
  pending: 'neutral',
  running: 'info',
  done: 'ok',
  failed: 'danger',
  killed: 'danger',
}

/** Genre d'un run de dispatch (colonne dispatch_jobs.kind). La task est neutre (le cas courant) ; le
 *  reviewer-de-gate ressort en `accent` (distinct, jamais confondu avec la task qu'il ancre), toolchain
 *  en info, fix en warn. */
export const JOB_KIND_TONE: Record<string, Tone> = {
  task: 'neutral',
  review: 'accent',
  toolchain: 'info',
  fix: 'warn',
}

/** Sévérité d'un finding de gate (🔴 rouge / 🟡 jaune / 🟣 violet). */
export const GATE_SEVERITY_TONE: Record<'red' | 'yellow' | 'purple', Tone> = {
  red: 'danger',
  yellow: 'warn',
  purple: 'purple',
}

/** État de run d'un déploiement (P5, colonne `deployments.status`). Dégradation **non verte** : `unhealthy`
 *  en danger, `stopped`/`no_deploy` en neutre — seul `running` est vert (jamais de faux-vert sur un service
 *  arrêté ou en panne). `building` en info (transitoire). */
export const DEPLOYMENT_STATUS_TONE: Record<string, Tone> = {
  no_deploy: 'neutral',
  building: 'info',
  running: 'ok',
  stopped: 'neutral',
  unhealthy: 'danger',
}

/** Ton d'un état de déploiement (repli neutre — jamais un faux-vert sur un état inconnu). */
export function deploymentTone(status: string): Tone {
  return DEPLOYMENT_STATUS_TONE[status] ?? 'neutral'
}

/** Ton d'une branche du SoT (vue Git) : réfs protégées distinctes (main=ok, dev=info), features en accent. */
export function gitBranchTone(name: string): Tone {
  if (name === 'main') return 'ok'
  if (name === 'dev') return 'info'
  if (name.startsWith('feature/')) return 'accent'
  return 'neutral'
}

/** État de sync SoT↔miroir GitHub (rollup projet ou par-branche) → Tone. Les dégradations restent
 *  **non vertes** (jamais de faux-vert) : `unreachable`/`no_mirror` en neutre, jamais `ok`. */
export const GIT_SYNC_TONE: Record<string, Tone> = {
  synced: 'ok',
  local_ahead: 'info',     // SoT en avance sur le miroir → à pousser (notre sens de marche : informatif)
  remote_ahead: 'warn',    // GitHub en avance → le SoT est périmé, à réconcilier (P5) : attention
  diverged: 'danger',      // non-ff → vraie divergence, bloquée (aucun auto-merge)
  unreachable: 'neutral',  // miroir injoignable/auth → honnête, surtout PAS vert
  no_mirror: 'neutral',    // aucun miroir câblé
}

/** Ton de l'état de sync miroir (repli neutre — jamais un faux-vert sur un état inconnu). */
export function syncTone(state: string): Tone {
  return GIT_SYNC_TONE[state] ?? 'neutral'
}

/** Action RÉELLEMENT appliquée à une branche par la réconciliation (résultat du POST) → Tone. Ce qui a
 *  avancé est vert/info ; les blocages restent **non verts** (danger/warn), jamais un faux-succès. */
export const GIT_RECONCILE_TONE: Record<string, Tone> = {
  fast_forward: 'ok',       // rattrapé sur GitHub
  pushed: 'ok',             // miroir mis à jour
  already_synced: 'ok',     // rien à faire, déjà aligné
  push_failed: 'danger',    // push best-effort échoué → honnête, surtout pas vert
  blocked_worktree: 'warn', // ff refusé (branche sortie) → à débloquer
  blocked_diverged: 'danger', // non-ff → réconciliation manuelle
}

/** Ton de l'action de réconciliation (repli neutre sur une action inconnue). */
export function reconcileTone(action: string): Tone {
  return GIT_RECONCILE_TONE[action] ?? 'neutral'
}

/** Résout une valeur métier en Tone via une map, avec repli neutre. */
export function toneFor(map: Record<string, Tone>, value: string | null | undefined): Tone {
  return (value && map[value]) || 'neutral'
}

/** Classes d'un badge (fond + texte + bordure) pour un ton. */
export function badgeClasses(tone: Tone): string {
  return TONE_BADGE[tone]
}

/** Classe d'une pastille (dot) pour un ton. */
export function dotClasses(tone: Tone): string {
  return TONE_DOT[tone]
}
