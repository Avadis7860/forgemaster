import { cn } from '@/lib/cn'

/** Bloc squelette (pulsation) pour le chargement de contenu structuré. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse-live rounded-card bg-surface-raised', className)} aria-hidden="true" />
}
