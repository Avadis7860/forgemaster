import { useId, useState, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Button } from './Button'

/** Dépli (disclosure) : un en-tête cliquable révèle/masque son contenu — pour ranger une section
 *  secondaire (config, détail) sans l'empiler en pleine hauteur. Non-contrôlé par défaut (`defaultOpen`),
 *  ou contrôlé (`open` + `onOpenChange`). Le déclencheur passe par la primitive Button (jamais un
 *  `<button>` brut — R1). Le contenu n'est monté que lorsqu'il est ouvert.
 *
 *  `variant='card'` (défaut) : cadre bordé — pour un bloc autonome (form, config) qui mérite son relief.
 *  `variant='section'` : en-tête léger façon libellé de section (rail/sidebar), SANS cadre — le relief
 *  reste réservé aux items (hover/actif), jamais au panneau (règle tissu > panneau). */
export function Collapsible({
  title, defaultOpen = false, open, onOpenChange, children, className, variant = 'card',
}: {
  title: ReactNode
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
  children: ReactNode
  className?: string
  variant?: 'card' | 'section'
}) {
  const [internal, setInternal] = useState(defaultOpen)
  const isOpen = open ?? internal
  const panelId = useId()
  const isSection = variant === 'section'
  const toggle = () => {
    const next = !isOpen
    if (open === undefined) setInternal(next)
    onOpenChange?.(next)
  }
  return (
    <div className={cn(!isSection && 'overflow-hidden rounded-card border border-border bg-surface', className)}>
      <Button
        variant="ghost"
        size="sm"
        onClick={toggle}
        aria-expanded={isOpen}
        aria-controls={panelId}
        className={cn('w-full justify-between rounded-none', isSection && 'h-auto px-2 py-1')}
      >
        <span
          className={cn(
            'font-medium text-fg',
            isSection && 'text-xs font-semibold uppercase tracking-wider text-muted',
          )}
        >
          {title}
        </span>
        <span aria-hidden className={cn('text-faint transition-transform', isOpen && 'rotate-90')}>▸</span>
      </Button>
      {isOpen && (
        <div id={panelId} className={cn(isSection ? 'px-1 pb-1 pt-0.5' : 'border-t border-border px-4 py-3')}>
          {children}
        </div>
      )}
    </div>
  )
}
