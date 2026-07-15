import { useState } from 'react'
import { Alert, EmptyState, LoadingState, RefreshButton, Segmented } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useDeploymentLogs } from '@/lib/queries'

type TailOpt = '100' | '500'
const TAILS = [
  { value: '100', label: '100 lignes' },
  { value: '500', label: '500 lignes' },
] as const satisfies ReadonlyArray<{ value: TailOpt; label: string }>

/** Log viewer borné : tail à la demande (100/500), `<pre>` mono scrollable. Vide honnête (jamais monté /
 *  rien journalisé). Query pilotée par l'ouverture du panneau (jamais auto). Extrait de l'ex-onglet Runtime
 *  pour être réutilisé dans le bandeau Runtime compact de la surface Ops. */
export function LogViewer({ project, branch, mounted }: { project: string; branch: string; mounted: boolean }) {
  const [tail, setTail] = useState<TailOpt>('100')
  const logs = useDeploymentLogs(project, branch, Number(tail), mounted)

  if (!mounted) {
    return <EmptyState title="Aucun log" description="Ce déploiement n'a jamais été monté." />
  }
  return (
    <div className="space-y-2 rounded-card border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted">Logs (dernières lignes)</span>
        <Segmented ariaLabel="Nombre de lignes" options={TAILS} value={tail} onChange={setTail} />
        <RefreshButton className="ml-auto" onClick={() => void logs.refetch()} busy={logs.isFetching} />
      </div>
      {logs.isError ? (
        <Alert tone="danger" title="Logs indisponibles">
          {logs.error instanceof ApiError ? logs.error.detail : String(logs.error)}
        </Alert>
      ) : logs.data && logs.data.lines.length > 0 ? (
        <pre className="max-h-80 overflow-auto rounded bg-faint/5 p-3 font-mono text-xs leading-relaxed text-muted">
          {logs.data.lines.join('\n')}
        </pre>
      ) : logs.isFetching ? (
        <LoadingState label="Lecture des logs…" />
      ) : (
        <EmptyState title="Aucune ligne" description="Le service n'a encore rien journalisé." />
      )}
    </div>
  )
}
