import { useParams } from '@tanstack/react-router'
import { Alert, Badge, Card, EmptyState, LoadingState, RefreshButton } from '@/components/ui'
import { ProjectCredentialCard } from '@/components/credential/ProjectCredentialCard'
import { ApiError } from '@/lib/api'
import { useGit } from '@/lib/queries'
import { gitBranchTone } from '@/lib/statusTone'
import type { GitAheadBehind, GitBranch, GitLogEntry } from '@/lib/schemas'

/** Onglet Git : visibilité read-only sur le SoT bare du projet — l'avance/retard `main` vs `dev` (le signal
 *  « main rattrape dev »), les branches, et le log court par réf protégée. Aucune action mutante (le cycle
 *  git vit dans le Gate) ; une seule lecture idempotente sert toute la vue. */
export function GitTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { data, isLoading, isError, error, refetch, isFetching } = useGit(project)

  if (isLoading) return <div className="p-8"><LoadingState label="Lecture du dépôt…" /></div>
  if (isError || !data) {
    return (
      <div className="space-y-3 p-8">
        <Alert tone="danger" title="Vue Git indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
        <RefreshButton onClick={() => refetch()} busy={isFetching} />
      </div>
    )
  }

  const order = ['dev', 'main']
  const refs = order.filter((r) => data.logs[r]?.length)

  return (
    <div className="space-y-4 p-6">
      <div className="flex justify-end">
        <RefreshButton onClick={() => refetch()} busy={isFetching} />
      </div>

      {data.ahead_behind && <SyncBanner ab={data.ahead_behind} />}

      <ProjectCredentialCard project={project} />

      <Card className="space-y-3 p-5">
        <p className="text-sm font-medium text-fg">Branches</p>
        {data.branches.length === 0 ? (
          <EmptyState title="Aucune branche" description="Le SoT ne porte encore aucune branche." />
        ) : (
          <ul className="space-y-2">
            {data.branches.map((b) => <BranchRow key={b.name} branch={b} />)}
          </ul>
        )}
      </Card>

      {refs.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {refs.map((ref) => <LogCard key={ref} refName={ref} entries={data.logs[ref]} />)}
        </div>
      )}
    </div>
  )
}

/** Bannière de synchro dev↔main : le signal produit « main rattrape dev » (dev mène, main suit en ff). */
function SyncBanner({ ab }: { ab: GitAheadBehind }) {
  const aligned = ab.ahead === 0 && ab.behind === 0
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3 p-5">
      <div className="space-y-1">
        <p className="text-sm font-medium text-fg">
          {aligned ? 'main est à jour avec dev' : `main est en retard de ${ab.ahead} commit(s) sur dev`}
        </p>
        <p className="text-sm text-muted">
          {aligned
            ? 'Les deux branches protégées pointent le même commit — rien à promouvoir.'
            : 'dev mène ; main rattrapera au prochain merge promu (ff dev→main).'}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Badge tone={ab.ahead > 0 ? 'info' : 'ok'} dot>dev +{ab.ahead}</Badge>
        <Badge tone={ab.behind > 0 ? 'warn' : 'ok'} dot>main −{ab.behind}</Badge>
      </div>
    </Card>
  )
}

/** Une branche : nom (ton par réf) + sha court mono + sujet du commit de tête. */
function BranchRow({ branch }: { branch: GitBranch }) {
  return (
    <li className="flex items-center gap-3">
      <Badge tone={gitBranchTone(branch.name)}>{branch.name}</Badge>
      <code className="shrink-0 font-mono text-xs text-muted">{branch.sha}</code>
      <span className="truncate text-sm text-muted" title={branch.subject}>{branch.subject}</span>
    </li>
  )
}

/** Log court d'une réf (récents d'abord) : sha court mono + sujet. */
function LogCard({ refName, entries }: { refName: string; entries: GitLogEntry[] }) {
  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center gap-2">
        <Badge tone={gitBranchTone(refName)}>{refName}</Badge>
        <span className="text-xs text-faint">{entries.length} commit(s)</span>
      </div>
      <ol className="space-y-1.5">
        {entries.map((e) => (
          <li key={e.sha} className="flex items-baseline gap-2">
            <code className="shrink-0 font-mono text-xs text-muted">{e.sha}</code>
            <span className="truncate text-sm text-fg" title={e.subject}>{e.subject}</span>
          </li>
        ))}
      </ol>
    </Card>
  )
}
