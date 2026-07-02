import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/** Sur-titre discret (uppercase, tracking) — primitive typo de la charte. */
export function Eyebrow({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <p className={cn('text-xs font-semibold uppercase tracking-wider text-faint', className)}>{children}</p>
  )
}
