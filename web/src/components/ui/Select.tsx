import type { SelectHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

/** Menu déroulant natif, aligné sur les tokens (hauteur/rayon/focus) — même gabarit que `Input`. Natif
 *  `<select>` : accessible par défaut, et le registre des types grandit (préféré à un `Segmented` figé). */
export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        'h-10 w-full rounded-card border border-border bg-bg px-3 text-sm text-fg',
        'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-500',
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  )
}
