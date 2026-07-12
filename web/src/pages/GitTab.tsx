import { useState } from 'react'
import { useParams } from '@tanstack/react-router'
import {
  Alert, Badge, Button, Card, Collapsible, EmptyState, LoadingState, RefreshButton, Segmented,
} from '@/components/ui'
import { ProjectCredentialCard } from '@/components/credential/ProjectCredentialCard'
import { RepoExplorer } from '@/components/git/RepoExplorer'
import { CommitDetailCard, DiffCard } from '@/components/git/GitIntelligence'
import { ApiError } from '@/lib/api'
import { useGit, useGitSync } from '@/lib/queries'
import { isLogUnified, syncSummary } from '@/lib/git'
import { gitBranchTone, syncTone } from '@/lib/statusTone'
import type { GitAheadBehind, GitBranch, GitLogEntry } from '@/lib/schemas'

type GitView = 'historique' | 'fichiers' | 'diff'

const VIEWS = [
  { value: 'historique', label: 'Historique' },
  { value: 'fichiers', label: 'Fichiers' },
  { value: 'diff', label: 'Diff' },
] as const satisfies ReadonlyArray<{ value: GitView; label: string }>

/** Onglet Git : visibilité read-only sur le SoT bare du projet. En-tête compact (réfs + SHA + synchro +
 *  config repliée) surmontant un sélecteur segmenté **[Historique · Fichiers · Diff]** — une vue à la fois,
 *  pour tenir dans un écran. Aucune action mutante (le cycle git vit dans le Gate) ; une seule lecture
 *  idempotente sert toute la vue. */
export function GitTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { data, isLoading, isError, error, refetch, isFetching } = useGit(project)
  // Sync miroir : RÉSEAU, manuel — jamais auto (enabled:false) ; le refresh manuel déclenche les DEUX
  // (vue read-only idempotente + fetch du miroir), pour que le badge reflète l'état après un clic.
  const sync = useGitSync(project)
  const onRefresh = () => { void refetch(); void sync.refetch() }
  const [sha, setSha] = useState<string | null>(null)  // commit sélectionné (clic sur log/branche)
  const [view, setView] = useState<GitView>('historique')

  if (isLoading) return <div className="p-8"><LoadingState label="Lecture du dépôt…" /></div>
  if (isError || !data) {
    return (
      <div className="space-y-3 p-8">
        <Alert tone="danger" title="Vue Git indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
        <RefreshButton onClick={onRefresh} busy={isFetching} />
      </div>
    )
  }

  const order = ['dev', 'main']
  const refs = order.filter((r) => data.logs[r]?.length)
  // « dev == main » : les deux réfs protégées pointent le même commit → leurs logs sont IDENTIQUES.
  // On n'en affiche alors qu'UN (pleine largeur) au lieu de deux colonnes redondantes.
  const unified = isLogUnified(data.ahead_behind, refs.length)
  const hasBranches = data.branches.length > 0
  const headSha = (data.branches.find((b) => b.name === 'dev') ?? data.branches[0])?.sha

  return (
    <div className="space-y-4 p-6">
      {/* En-tête compact : réfs + SHA de tête + état de synchro + rafraîchir. */}
      <div className="flex flex-wrap items-center gap-2">
        {data.branches.map((b) => (
          <Badge key={b.name} tone={gitBranchTone(b.name)}>{b.name}</Badge>
        ))}
        {headSha && <code className="font-mono text-xs text-faint">{headSha}</code>}
        {data.ahead_behind && <SyncChip ab={data.ahead_behind} />}
        <RemoteSyncChip sync={sync} />
        <RefreshButton className="ml-auto" onClick={onRefresh} busy={isFetching || sync.isFetching} />
      </div>

      {/* Config miroir/token = réglage, pas lecture → repliée par défaut pour rendre la hauteur au dépôt. */}
      <Collapsible title="Miroir GitHub & token de push">
        <ProjectCredentialCard project={project} bare />
      </Collapsible>

      <Segmented ariaLabel="Vue Git" options={VIEWS} value={view} onChange={setView} />

      {view === 'historique' && (
        <div className="space-y-4">
          {sha && <CommitDetailCard project={project} sha={sha} onClose={() => setSha(null)} />}
          <Card className="space-y-3 p-5">
            <p className="text-sm font-medium text-fg">Branches</p>
            {hasBranches ? (
              <ul className="space-y-2">
                {data.branches.map((b) => <BranchRow key={b.name} branch={b} onSelect={setSha} />)}
              </ul>
            ) : (
              <EmptyState title="Aucune branche" description="Le SoT ne porte encore aucune branche." />
            )}
          </Card>
          {unified ? (
            <LogCard refName="dev" entries={data.logs.dev} onSelect={setSha} alsoRef="main" />
          ) : refs.length > 0 ? (
            <div className="grid gap-4 md:grid-cols-2">
              {refs.map((ref) => (
                <LogCard key={ref} refName={ref} entries={data.logs[ref]} onSelect={setSha} />
              ))}
            </div>
          ) : null}
        </div>
      )}

      {view === 'fichiers' && (
        hasBranches ? (
          <RepoExplorer project={project} branches={data.branches} />
        ) : (
          <EmptyState title="Aucun fichier" description="Le SoT ne porte encore aucune branche à explorer." />
        )
      )}

      {view === 'diff' && (
        hasBranches ? (
          <DiffCard project={project} branches={data.branches} />
        ) : (
          <EmptyState title="Rien à comparer" description="Le SoT ne porte encore aucune branche." />
        )
      )}
    </div>
  )
}

/** Puce de synchro dev↔main compacte (en-tête) : « à jour » si alignées, sinon l'écart. */
function SyncChip({ ab }: { ab: GitAheadBehind }) {
  if (ab.ahead === 0 && ab.behind === 0) {
    return <Badge tone="ok" dot>à jour</Badge>
  }
  return (
    <span className="inline-flex items-center gap-1.5" title={`dev mène de ${ab.ahead} commit(s)`}>
      <Badge tone="warn" dot>main −{ab.ahead}</Badge>
    </span>
  )
}

/** Puce de synchro SoT↔**miroir GitHub** (RÉSEAU). Rendue seulement APRÈS une vérif manuelle (le fetch n'est
 *  pas auto) — avant, une invite discrète « miroir ? ». Chaque état a son ton (jamais de faux-vert :
 *  injoignable / pas-de-miroir restent neutres, jamais `ok`). Le `title` détaille l'écart par branche. */
function RemoteSyncChip({ sync }: { sync: ReturnType<typeof useGitSync> }) {
  if (sync.isFetching) return <Badge tone="neutral" dot>miroir…</Badge>
  const data = sync.data
  if (!data) {
    return (
      <span title="Clique ⟳ pour vérifier la synchro avec le miroir GitHub">
        <Badge tone="neutral" className="opacity-60">miroir ?</Badge>
      </span>
    )
  }
  const detail = Object.entries(data.branches)
    .map(([b, s]) => `${b} : +${s.ahead} / −${s.behind}`).join(' · ')
  return (
    <span className="inline-flex items-center" title={detail || `miroir ${data.remote} : ${data.state}`}>
      <Badge tone={syncTone(data.state)} dot>{syncSummary(data)}</Badge>
    </span>
  )
}

/** Une branche : nom (ton par réf) + sha court mono + sujet, cliquable pour ouvrir le détail du commit de
 *  tête (via la primitive Button, jamais un bouton HTML brut — R1). */
function BranchRow({ branch, onSelect }: { branch: GitBranch; onSelect: (sha: string) => void }) {
  return (
    <li>
      <Button variant="ghost" size="sm" onClick={() => onSelect(branch.sha)}
        className="w-full justify-start gap-3">
        <Badge tone={gitBranchTone(branch.name)}>{branch.name}</Badge>
        <code className="shrink-0 font-mono text-xs text-muted">{branch.sha}</code>
        <span className="truncate text-sm text-muted" title={branch.subject}>{branch.subject}</span>
      </Button>
    </li>
  )
}

/** Log court d'une réf (récents d'abord) : sha court mono + sujet, chaque entrée cliquable pour son détail.
 *  `alsoRef` (mode unifié dev==main) : affiche une 2ᵉ réf + « identiques » — un seul log pour les deux. */
function LogCard({ refName, entries, onSelect, alsoRef }: {
  refName: string
  entries: GitLogEntry[]
  onSelect: (sha: string) => void
  alsoRef?: string
}) {
  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center gap-2">
        <Badge tone={gitBranchTone(refName)}>{refName}</Badge>
        {alsoRef && <Badge tone={gitBranchTone(alsoRef)}>{alsoRef}</Badge>}
        <span className="text-xs text-faint">
          {entries.length} commit(s){alsoRef ? ' · branches identiques' : ''}
        </span>
      </div>
      <ol className="space-y-0.5">
        {entries.map((e) => (
          <li key={e.sha}>
            <Button variant="ghost" size="sm" onClick={() => onSelect(e.sha)}
              className="w-full items-baseline justify-start gap-2">
              <code className="shrink-0 font-mono text-xs text-muted">{e.sha}</code>
              <span className="truncate text-sm text-fg" title={e.subject}>{e.subject}</span>
            </Button>
          </li>
        ))}
      </ol>
    </Card>
  )
}
