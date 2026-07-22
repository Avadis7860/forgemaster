import { lazy, Suspense, useEffect } from 'react'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { Button, Dialog, LoadingState } from '@/components/ui'
import { RuntimeStrip } from '@/components/runtime/RuntimeStrip'
import { FlowPanel } from '@/components/flow/FlowPanel'
import { FrontmapPanel } from '@/components/frontmap/FrontmapPanel'
import type { PtySession } from '@/lib/ws'

// xterm (~290 kB) code-split hors du bundle principal, comme l'ex-onglet Terminal.
const TerminalPane = lazy(() =>
  import('@/components/terminal/TerminalPane').then((m) => ({ default: m.TerminalPane })),
)

type Panel = 'flow' | 'frontmap'

/** Onglet « Ops » : opérer & inspecter le système qui tourne, en UNE vue. **Terminal = ancre** (remplit
 *  l'espace), **Runtime réduit/fondu** en bandeau glanceable au-dessus. **Flow** (modal) s'ouvre AU CLIC
 *  (consent-gated) — l'info dense/exploratoire n'apparaît pas sans demande. Overlay piloté par l'URL
 *  `?panel=flow` → deep-linkable (capturable at-rest par le juge visuel). **Git** n'est plus un drawer ici :
 *  il est promu en **surface de plein droit** `/git` (rail Corpus) — l'ancien `?panel=git` y redirige. */
export function OpsTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { panel, run } = useSearch({ strict: false }) as { panel?: string; run?: string }
  const navigate = useNavigate()
  // Handoff depuis le dispatch d'un socle interactif (`?run=interview`) : le pane se branche sur la session
  // PTY **interview** (dédiée, son process EST `cockpit interview`), sinon sur le **shell** de login. Le
  // search param d'URL EST le canal (deep-linkable, capturable at-rest) — il sélectionne la SESSION, plus
  // aucune commande n'est tapée dans un shell partagé (l'ancien modèle cassait dès que le shell persistait).
  const session: PtySession = run === 'interview' ? 'interview' : 'shell'

  // Redirection douce : le drawer Git a été retiré (promu en surface `/git`). Un ancien lien `?panel=git`
  // (bookmark) renvoie vers la destination de plein droit, en portant le projet courant.
  useEffect(() => {
    if (panel === 'git') {
      navigate({ to: '/git', replace: true, search: () => (project ? { project } : {}) })
    }
  }, [panel, project, navigate])

  // Ouverture/fermeture par updater fonctionnel → préserve les autres params (ex. `?run=`).
  const openPanel = (p: Panel) =>
    navigate({ to: '/$project/ops', params: { project }, search: (prev) => ({ ...prev, panel: p }) })
  const closePanel = () =>
    navigate({ to: '/$project/ops', params: { project }, search: (prev) => ({ ...prev, panel: undefined }) })

  return (
    <div className="flex h-full flex-col gap-3 p-4 sm:p-6">
      {/* Bandeau : Runtime compact (grandit) + déclencheur consenti (à droite au desktop, empilé sous la
          carte à 390 pour lui rendre la pleine largeur). */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 sm:flex-1"><RuntimeStrip project={project} /></div>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" onClick={() => openPanel('flow')} aria-haspopup="dialog">Flow</Button>
          <Button variant="secondary" onClick={() => openPanel('frontmap')} aria-haspopup="dialog">Frontmap</Button>
        </div>
      </div>

      {/* Terminal = ancre, remplit le reste de la hauteur. */}
      <div className="min-h-0 flex-1">
        <Suspense fallback={<LoadingState label="Chargement du terminal…" />}>
          <TerminalPane key={`${project}:${session}`} project={project} session={session} />
        </Suspense>
      </div>

      {/* Flow = modal centré : regarde le call-flow puis ferme. */}
      <Dialog open={panel === 'flow'} onOpenChange={(o) => { if (!o) closePanel() }}
        side="center" title="Flow · call-flow">
        <FlowPanel project={project} />
      </Dialog>

      {/* Frontmap = modal centré : inspecte le design-system indexé (tokens/primitives/routes) puis ferme. */}
      <Dialog open={panel === 'frontmap'} onOpenChange={(o) => { if (!o) closePanel() }}
        side="center" title="Frontmap · design-system">
        <FrontmapPanel project={project} />
      </Dialog>
    </div>
  )
}
