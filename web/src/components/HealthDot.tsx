import { useHealth } from '@/lib/queries'
import { cn } from '@/lib/cn'

/** Pastille d'état du daemon dans le header : vert = up (+ version), rouge = injoignable. */
export function HealthDot() {
  const { data, isError, isPending } = useHealth()
  const tone = isPending ? 'bg-faint' : isError ? 'bg-danger-500' : 'bg-ok-500'
  const label = isPending ? 'daemon…' : isError ? 'daemon injoignable' : `daemon v${data.version}`
  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted">
      <span className={cn('size-2 rounded-pill', tone, !isError && !isPending && 'animate-pulse-live')} />
      {label}
    </span>
  )
}
