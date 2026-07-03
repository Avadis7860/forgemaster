import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Button } from './Button'

/** Sélecteur segmenté (une vue à la fois) : un groupe de segments dont un seul est actif. Chaque segment
 *  passe par la primitive Button (jamais un `<button>` brut — R1) ; l'actif est « surélevé » (secondary),
 *  les autres discrets (ghost). `role=tablist`/`tab` + `aria-selected` pour l'accessibilité. */
export function Segmented<T extends string>({ options, value, onChange, className, ariaLabel }: {
  options: ReadonlyArray<{ value: T; label: ReactNode }>
  value: T
  onChange: (value: T) => void
  className?: string
  ariaLabel?: string
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn('inline-flex gap-1 rounded-card border border-border bg-surface p-1', className)}
    >
      {options.map((o) => {
        const active = o.value === value
        return (
          <Button
            key={o.value}
            role="tab"
            aria-selected={active}
            variant={active ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => onChange(o.value)}
            className={cn('border-transparent', active && 'border-border')}
          >
            {o.label}
          </Button>
        )
      })}
    </div>
  )
}
