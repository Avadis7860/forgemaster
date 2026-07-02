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
