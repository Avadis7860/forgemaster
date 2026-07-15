import type { ReactNode } from 'react'
import * as RD from '@radix-ui/react-dialog'
import { cn } from '@/lib/cn'
import { Button } from './Button'

/** Dialog accessible (radix-ui : focus-trap, ESC, `aria-modal`, focus-return, scroll-lock — pattern WAI-ARIA
 *  APG). Deux formes via `side` : `center` = modal centré (regarde-et-ferme), `right` = drawer latéral (le fond
 *  reste VISIBLE sous un scrim léger → inspecter en gardant le contexte). Contrôlé (`open`/`onOpenChange`) pour
 *  piloter l'ouverture par l'URL (deep-link). Trigger/close via la primitive Button (R1 : jamais un `<button>`
 *  brut). Scrim + surface au-dessus de tout via le token `z-(--z-overlay)`. Plein-écran sous `sm` (axe 8). */
export function Dialog({
  open, onOpenChange, title, side = 'center', children, className,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  side?: 'center' | 'right'
  children: ReactNode
  className?: string
}) {
  const drawer = side === 'right'
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        {/* Scrim : léger pour le drawer (le fond reste lisible), franc pour le modal. */}
        <RD.Overlay className={cn('fixed inset-0 z-(--z-overlay)', drawer ? 'bg-black/30' : 'bg-black/55')} />
        <RD.Content
          aria-describedby={undefined}
          className={cn(
            'fixed z-(--z-overlay) flex flex-col bg-surface shadow-overlay focus:outline-none',
            drawer
              // Drawer : recouvre toute la zone de contenu jusqu'au rail (w-72 = 18rem) au desktop ; plein-écran
              // sous `md` (le rail est alors off-canvas, le contenu prend déjà 100 %).
              ? 'inset-y-0 right-0 w-screen border-l border-border md:w-[calc(100vw-18rem)]'
              : 'left-1/2 top-1/2 w-[min(860px,94vw)] max-h-[88vh] -translate-x-1/2 -translate-y-1/2 rounded-card border border-border ' +
                'max-sm:inset-0 max-sm:left-0 max-sm:top-0 max-sm:h-screen max-sm:max-h-none max-sm:w-screen max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-none',
            className,
          )}
        >
          <div className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-3">
            <RD.Title className="font-semibold text-fg">{title}</RD.Title>
            {/* Fermeture via onClick direct (dialog contrôlé) : évite `RD.Close asChild` qui exigerait un
                Button forwardRef (Slot pose une ref) — on garde la primitive Button sans toucher son API. */}
            <Button variant="ghost" size="sm" aria-label="Fermer" className="ml-auto"
              onClick={() => onOpenChange(false)}>✕</Button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-4">{children}</div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}
