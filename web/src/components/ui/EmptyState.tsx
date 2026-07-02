import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/** État vide — jamais une page blanche : titre + explication + action possible. */
export function EmptyState({ title, description, action, className }: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn(
      'flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border',
      'bg-surface/40 px-6 py-12 text-center', className,
    )}>
      <p className="text-sm font-medium text-fg">{title}</p>
      {description && <p className="max-w-sm text-sm text-muted">{description}</p>}
      {action}
    </div>
  )
}
