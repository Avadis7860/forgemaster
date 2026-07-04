import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { Alert, EmptyState, LoadingState, RefreshButton } from '@/components/ui'
import { FlowGraph } from '@/components/flow/FlowGraph'
import { useFlow, useFlowOperations } from '@/lib/queries'

/** Onglet Flow : le FLOT D'EXÉCUTION d'une opération (route API ou verbe CLI) du projet, rendu par
 *  `<FlowGraph/>` depuis l'index code-map (boîte-noire). Le sélecteur liste les opérations découvertes ;
 *  `?op=<entry>` rend la vue deep-linkable (goto-safe pour la boucle visuelle). L'`entry` (`file::qualname`,
 *  UNIQUE) sert d'identité — PAS le label : deux opérations peuvent partager un label (ex. `cli:main` dans
 *  deux fichiers), et l'adresser par label en masquerait une (le backend résout le 1ᵉʳ match). */
export function FlowTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { op } = useSearch({ strict: false }) as { op?: string }
  const navigate = useNavigate()

  const ops = useFlowOperations(project)
  const operations = ops.data?.operations ?? []
  // Sélection par `entry` (unique) : ?op= si présent, sinon la 1ʳᵉ route (frontière la plus parlante), sinon
  // la 1ʳᵉ op. On tolère un ?op= désormais introuvable (index nettoyé/rebuild) en retombant sur le défaut.
  const known = operations.some((o) => o.entry === op)
  const selected =
    (known ? op : undefined) ?? operations.find((o) => o.kind === 'route')?.entry ?? operations[0]?.entry ?? ''
  const flow = useFlow(project, selected)

  const setOp = (value: string) =>
    navigate({ to: '/$project/flow', params: { project }, search: { op: value }, replace: true })

  // Deux opérations peuvent porter le même label → on montre le fichier pour désambiguïser à l'œil.
  const fileOf = (entry: string) => entry.split('::')[0]

  if (ops.isLoading) return <div className="p-8"><LoadingState label="Découverte des opérations…" /></div>
  if (ops.isError) {
    return (
      <div className="space-y-3 p-8">
        <Alert tone="danger" title="Opérations indisponibles">{ops.error.message}</Alert>
        <RefreshButton onClick={() => ops.refetch()} busy={ops.isFetching} />
      </div>
    )
  }
  if (operations.length === 0) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          title="Aucune opération"
          description="code-map n'a découvert aucune route API ni verbe CLI dans ce projet (rien à cartographier)."
        />
      </div>
    )
  }

  const routes = operations.filter((o) => o.kind === 'route')
  const clis = operations.filter((o) => o.kind === 'cli')

  return (
    <div className="space-y-4 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm font-medium text-muted" htmlFor="flow-op">Opération</label>
        <select
          id="flow-op"
          value={selected}
          onChange={(e) => setOp(e.target.value)}
          className="min-w-72 max-w-full rounded-card border border-border bg-surface px-2.5 py-1.5 text-sm text-fg"
        >
          {routes.length > 0 && (
            <optgroup label="Routes API">
              {routes.map((o) => (
                <option key={`${o.kind}:${o.operation}:${o.entry}`} value={o.entry}>
                  {o.operation} — {fileOf(o.entry)}
                </option>
              ))}
            </optgroup>
          )}
          {clis.length > 0 && (
            <optgroup label="Verbes CLI">
              {clis.map((o) => (
                <option key={`${o.kind}:${o.operation}:${o.entry}`} value={o.entry}>
                  {o.operation} — {fileOf(o.entry)}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <span className="text-xs tabular-nums text-faint">{operations.length} opérations</span>
        <RefreshButton className="ml-auto" onClick={() => flow.refetch()} busy={flow.isFetching} />
      </div>

      {flow.isLoading ? (
        <LoadingState label="Extraction du flot…" />
      ) : flow.isError ? (
        <Alert tone="danger" title="Flot indisponible">{flow.error.message}</Alert>
      ) : flow.data ? (
        <FlowGraph flow={flow.data} />
      ) : null}
    </div>
  )
}
