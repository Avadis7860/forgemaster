import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { badgeClasses, dotClasses, type Tone } from '@/lib/statusTone'

/** Pastille de statut — tons via la source unique statusTone (jamais de couleur inline). `title` = infobulle
 *  native optionnelle (lève l'ambiguïté d'un libellé court au survol), sans couplage sémantique. */
export function Badge({ tone = 'neutral', dot = false, className, title, children }: {
  tone?: Tone
  dot?: boolean
  className?: string
  title?: string
  children: ReactNode
}) {
  return (
    <span title={title} className={cn(
      'inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 text-xs font-medium',
      badgeClasses(tone), className,
    )}>
      {dot && <span className={cn('size-1.5 rounded-pill', dotClasses(tone))} />}
      {children}
    </span>
  )
}
