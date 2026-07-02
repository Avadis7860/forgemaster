import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { badgeClasses, dotClasses, type Tone } from '@/lib/statusTone'

/** Pastille de statut — tons via la source unique statusTone (jamais de couleur inline). */
export function Badge({ tone = 'neutral', dot = false, className, children }: {
  tone?: Tone
  dot?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 text-xs font-medium',
      badgeClasses(tone), className,
    )}>
      {dot && <span className={cn('size-1.5 rounded-pill', dotClasses(tone))} />}
      {children}
    </span>
  )
}
