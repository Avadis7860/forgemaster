import { useId, useState, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Button } from './Button'

/** Dépli (disclosure) : un en-tête cliquable révèle/masque son contenu — pour ranger une section
 *  secondaire (config, détail) sans l'empiler en pleine hauteur. Non-contrôlé par défaut (`defaultOpen`),
 *  ou contrôlé (`open` + `onOpenChange`). Le déclencheur passe par la primitive Button (jamais un
 *  `<button>` brut — R1). Le contenu n'est monté que lorsqu'il est ouvert. */
export function Collapsible({
  title, defaultOpen = false, open, onOpenChange, children, className,
}: {
  title: ReactNode
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
  children: ReactNode
  className?: string
}) {
  const [internal, setInternal] = useState(defaultOpen)
  const isOpen = open ?? internal
  const panelId = useId()
  const toggle = () => {
    const next = !isOpen
    if (open === undefined) setInternal(next)
    onOpenChange?.(next)
  }
  return (
    <div className={cn('overflow-hidden rounded-card border border-border bg-surface', className)}>
      <Button
        variant="ghost"
        size="sm"
        onClick={toggle}
        aria-expanded={isOpen}
        aria-controls={panelId}
        className="w-full justify-between rounded-none"
      >
        <span className="font-medium text-fg">{title}</span>
        <span aria-hidden className={cn('text-faint transition-transform', isOpen && 'rotate-90')}>▸</span>
      </Button>
      {isOpen && (
        <div id={panelId} className="border-t border-border px-4 py-3">
          {children}
        </div>
      )}
    </div>
  )
}
