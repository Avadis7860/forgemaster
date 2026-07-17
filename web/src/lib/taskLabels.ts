// Libellés FR des états DAG du résolveur (source unique d'affichage ; les tons vivent dans statusTone).
export const TASK_STATE_LABEL: Record<string, string> = {
  DONE: 'fait',
  ACTIVE: 'en cours',
  READY: 'prêt',
  BLOCKED_DEPS: 'bloqué',
  BLOCKED: 'bloqué',
  CYCLE: 'cycle',
  ERROR: 'erreur',
  CANCELLED: 'annulé',
}

export function stateLabel(state: string): string {
  return TASK_STATE_LABEL[state] ?? state
}

// Libellés FR du statut d'un job de dispatch (colonne dispatch_jobs).
export const JOB_STATUS_LABEL: Record<string, string> = {
  pending: 'en attente',
  running: 'en cours',
  done: 'terminé',
  failed: 'échoué',
  killed: 'interrompu',
}

export function jobStatusLabel(status: string): string {
  return JOB_STATUS_LABEL[status] ?? status
}

// Libellés FR du GENRE d'un run (colonne dispatch_jobs.kind) : distingue le worker-de-task du
// reviewer-de-gate et des runs toolchain/fix — sans quoi un run review s'affiche sous le slug de la task.
export const JOB_KIND_LABEL: Record<string, string> = {
  task: 'task',
  review: 'review',
  toolchain: 'toolchain',
  fix: 'fix',
}

export function jobKindLabel(kind: string): string {
  return JOB_KIND_LABEL[kind] ?? kind
}
