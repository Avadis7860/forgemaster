import { lazy, Suspense } from 'react'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { Button, Dialog, LoadingState } from '@/components/ui'
import { RuntimeStrip } from '@/components/runtime/RuntimeStrip'
import { GitPanel } from '@/components/git/GitPanel'
import { FlowPanel } from '@/components/flow/FlowPanel'

// xterm (~290 kB) code-split hors du bundle principal, comme l'ex-onglet Terminal.
const TerminalPane = lazy(() =>
  import('@/components/terminal/TerminalPane').then((m) => ({ default: m.TerminalPane })),
)

type Panel = 'git' | 'flow'

/** Onglet « Ops » : opérer & inspecter le système qui tourne, en UNE vue. **Terminal = ancre** (remplit
 *  l'espace), **Runtime réduit/fondu** en bandeau glanceable au-dessus. **Git** (drawer latéral) et **Flow**
 *  (modal) s'ouvrent AU CLIC (consent-gated) — l'info dense/exploratoire n'apparaît pas sans demande. Overlays
 *  pilotés par l'URL `?panel=git|flow` → deep-linkables (capturables at-rest par le juge visuel). */
export function OpsTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { panel, run } = useSearch({ strict: false }) as { panel?: Panel; run?: string }
  const navigate = useNavigate()
  // Handoff depuis le dispatch d'un socle interactif (`?run=interview`) : le terminal auto-lance la commande
  // d'interview à l'ouverture. Le search param d'URL EST le canal (deep-linkable, capturable at-rest).
  const initialCommand = run === 'interview' && project ? `cockpit interview ${project}` : undefined
  // Ouverture/fermeture par updater fonctionnel → préserve les autres params (ex. `?op=` de Flow).
  const openPanel = (p: Panel) =>
    navigate({ to: '/$project/ops', params: { project }, search: (prev) => ({ ...prev, panel: p }) })
  const closePanel = () =>
    navigate({ to: '/$project/ops', params: { project }, search: (prev) => ({ ...prev, panel: undefined }) })

  return (
    <div className="flex h-full flex-col gap-3 p-4 sm:p-6">
      {/* Bandeau : Runtime compact (grandit) + déclencheurs consentis (à droite au desktop, empilés sous la
          carte à 390 pour lui rendre la pleine largeur). */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 sm:flex-1"><RuntimeStrip project={project} /></div>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" onClick={() => openPanel('git')} aria-haspopup="dialog">Git</Button>
          <Button variant="secondary" onClick={() => openPanel('flow')} aria-haspopup="dialog">Flow</Button>
        </div>
      </div>

      {/* Terminal = ancre, remplit le reste de la hauteur. */}
      <div className="min-h-0 flex-1">
        <Suspense fallback={<LoadingState label="Chargement du terminal…" />}>
          <TerminalPane key={project} project={project} initialCommand={initialCommand} />
        </Suspense>
      </div>

      {/* Git = drawer latéral droit : le Terminal reste visible sous un scrim léger (inspecter en opérant). */}
      <Dialog open={panel === 'git'} onOpenChange={(o) => { if (!o) closePanel() }}
        side="right" title={`Git · ${project}`}>
        <GitPanel project={project} />
      </Dialog>

      {/* Flow = modal centré : regarde le call-flow puis ferme. */}
      <Dialog open={panel === 'flow'} onOpenChange={(o) => { if (!o) closePanel() }}
        side="center" title="Flow · call-flow">
        <FlowPanel project={project} />
      </Dialog>
    </div>
  )
}
