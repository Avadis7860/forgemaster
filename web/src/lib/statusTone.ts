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

/** Sévérité d'un finding de gate (🔴 rouge / 🟡 jaune / 🟣 violet). */
export const GATE_SEVERITY_TONE: Record<'red' | 'yellow' | 'purple', Tone> = {
  red: 'danger',
  yellow: 'warn',
  purple: 'purple',
}

/** Ton d'une branche du SoT (vue Git) : réfs protégées distinctes (main=ok, dev=info), features en accent. */
export function gitBranchTone(name: string): Tone {
  if (name === 'main') return 'ok'
  if (name === 'dev') return 'info'
  if (name.startsWith('feature/')) return 'accent'
  return 'neutral'
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
