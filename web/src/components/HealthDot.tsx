import { useHealth } from '@/lib/queries'
import { cn } from '@/lib/cn'

/**
 * Pastille d'état du daemon dans le header. TROIS états, pas deux : vert = il sert (+ version), rouge =
 * injoignable, ambre = il a démarré mais ne peut rien servir — et il dit pourquoi (`title` porte le motif
 * complet, avec les gestes qui débloquent).
 *
 * Le troisième état existe parce que sans lui il est indistinguable du premier : une instance dont la base
 * est illisible répondait 200 et s'affichait verte. L'utilisateur n'a pas de terminal ; si le produit ne le
 * dit pas ici, personne ne le lui dit.
 */
export function HealthDot() {
  const { data, isError, isPending } = useHealth()
  const unservable = !isPending && !isError && data.ready === false
  const tone = isPending ? 'bg-faint' : isError ? 'bg-danger-500' : unservable ? 'bg-warn-500' : 'bg-ok-500'
  const label = isPending
    ? 'daemon…'
    : isError
      ? 'daemon injoignable'
      : unservable
        ? 'daemon inservable'
        : `daemon v${data.version}`
  return (
    <span
      className="inline-flex items-center gap-2 text-xs text-muted"
      title={unservable ? data.detail : undefined}
    >
      <span
        className={cn('size-2 rounded-pill', tone, !isError && !isPending && !unservable && 'animate-pulse-live')}
      />
      {label}
    </span>
  )
}
