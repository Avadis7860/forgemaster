import { cn } from '@/lib/cn'
import { dotClasses, TASK_STATE_TONE, toneFor } from '@/lib/statusTone'
import { stateLabel } from '@/lib/taskLabels'

// Les états montrés dans la légende (ordre de lecture d'un flux : prêt → en cours → fait, puis blocages).
const LEGEND_STATES = ['READY', 'ACTIVE', 'DONE', 'BLOCKED_DEPS', 'CYCLE'] as const

/** Légende des tons d'état — rendue une fois en tête de la vue Roadmap. */
export function StateLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted">
      {LEGEND_STATES.map((s) => (
        <span key={s} className="flex items-center gap-1.5">
          <span className={cn('size-2 rounded-pill', dotClasses(toneFor(TASK_STATE_TONE, s)))} />
          {stateLabel(s)}
        </span>
      ))}
    </div>
  )
}
