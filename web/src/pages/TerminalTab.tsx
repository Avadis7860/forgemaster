import { Suspense, lazy } from 'react'
import { useParams } from '@tanstack/react-router'
import { Card, EmptyState, LoadingState, SectionTitle } from '@/components/ui'

// xterm.js est lourd (~290 kB) et n'est utile que sur cet onglet → code-split : le pane (et sa dépendance
// xterm) ne se charge QUE quand on ouvre le terminal, le bundle principal reste léger.
const TerminalPane = lazy(() =>
  import('@/components/terminal/TerminalPane').then((m) => ({ default: m.TerminalPane })),
)

/** Onglet Terminal : un login shell PTY dans la racine du projet, servi par `WS /ws/terminal/{project}`
 *  (backend déjà prêt). Vue directe sur la forge — inspecter le SoT/worktrees, lancer un git, etc. */
export function TerminalTab() {
  const project = useParams({ strict: false }).project ?? ''

  if (!project) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState title="Aucun projet" description="Ouvre un projet pour lancer son terminal." />
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      <SectionTitle eyebrow="shell" title="Terminal" />
      <Card className="p-4">
        <Suspense fallback={<LoadingState label="Chargement du terminal…" />}>
          <TerminalPane key={project} project={project} />
        </Suspense>
      </Card>
    </div>
  )
}
