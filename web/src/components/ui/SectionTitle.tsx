import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Eyebrow } from './Eyebrow'

/** Titre de section : sur-titre optionnel + h2 + slot d'actions à droite. */
export function SectionTitle({ eyebrow, title, actions, className }: {
  eyebrow?: ReactNode
  title: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex items-end justify-between gap-4', className)}>
      <div className="space-y-1">
        {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
        <h2 className="text-lg font-semibold text-fg">{title}</h2>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
