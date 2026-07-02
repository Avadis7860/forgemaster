import { cn } from '@/lib/cn'
import { Spinner } from './Spinner'

/** Bouton de rafraîchissement (icône) — montre l'état en cours de refetch. */
export function RefreshButton({ onClick, busy = false, className }: {
  onClick: () => void
  busy?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label="Rafraîchir"
      className={cn(
        'inline-flex size-8 items-center justify-center rounded-card border border-border text-muted',
        'hover:bg-surface-raised hover:text-fg disabled:opacity-50', className,
      )}
    >
      {busy ? <Spinner className="size-4" /> : <span aria-hidden="true">↻</span>}
    </button>
  )
}
