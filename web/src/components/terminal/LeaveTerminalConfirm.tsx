import { Button, Dialog } from '@/components/ui'

/** État minimal du resolver de `useBlocker` (TanStack Router) consommé ici — `proceed`/`reset` sont
 *  `undefined` hors état `blocked`, exactement comme le resolver réel. Ce sous-type rend le composant
 *  testable SANS routeur (on injecte un resolver factice). */
export type LeaveBlocker = { status: 'blocked' | 'idle'; proceed?: () => void; reset?: () => void }

/** Confirmation de départ quand une session terminale est active : quitter fermerait le WebSocket → le daemon
 *  tue le login shell (SIGHUP) et TOUTE commande en cours (ex. une interview de 1ʳᵉ session). Présentationnel
 *  et contrôlé par le resolver de `useBlocker` : `Rester` annule (`reset`), `Quitter` poursuit (`proceed`).
 *  Le travail déjà produit reste récupérable côté forge (worktree/DB) — cette garde évite juste la surprise. */
export function LeaveTerminalConfirm({ blocker }: { blocker: LeaveBlocker }) {
  const open = blocker.status === 'blocked'
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) blocker.reset?.() }} side="center"
      title="Quitter le terminal ?">
      <div className="flex flex-col gap-4">
        <p className="text-sm text-fg">
          Une session terminale est active. Quitter la fermera et interrompra toute commande en cours
          (par exemple une interview de 1ʳᵉ session).
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => blocker.reset?.()}>Rester</Button>
          <Button variant="primary" size="sm" onClick={() => blocker.proceed?.()}>Quitter quand même</Button>
        </div>
      </div>
    </Dialog>
  )
}
