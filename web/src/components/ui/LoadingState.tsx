import { cn } from '@/lib/cn'
import { Spinner } from './Spinner'

/** État de chargement centré — distinct de l'état vide (une donnée arrive vs il n'y en a pas). */
export function LoadingState({ label = 'Chargement…', className }: { label?: string; className?: string }) {
  return (
    <div className={cn('flex items-center justify-center gap-2 px-6 py-12 text-sm text-muted', className)}>
      <Spinner className="size-4" />
      {label}
    </div>
  )
}
