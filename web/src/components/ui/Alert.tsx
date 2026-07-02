import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { badgeClasses, type Tone } from '@/lib/statusTone'

/** Encart contextuel (erreur/info) — réutilise les tons de la source unique. */
export function Alert({ tone = 'info', title, className, children }: {
  tone?: Tone
  title?: ReactNode
  className?: string
  children?: ReactNode
}) {
  return (
    <div role="alert" className={cn('rounded-card border px-4 py-3 text-sm', badgeClasses(tone), className)}>
      {title && <p className="font-semibold">{title}</p>}
      {children && <div className={cn('opacity-90', Boolean(title) && 'mt-1')}>{children}</div>}
    </div>
  )
}
