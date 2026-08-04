import { useEffect, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import {
  Alert, Badge, Button, Card, Collapsible, EmptyState, LoadingState, RefreshButton, SectionTitle,
  Segmented, Select,
} from '@/components/ui'
import { ProjectCredentialCard } from '@/components/credential/ProjectCredentialCard'
import { RepoExplorer } from '@/components/git/RepoExplorer'
import { CommitDetailCard, DiffCard } from '@/components/git/GitIntelligence'
import { cn } from '@/lib/cn'
import { ApiError } from '@/lib/api'
import { useGit, useGitSync, useProjects, useReconcileSync } from '@/lib/queries'
import {
  isLogUnified, isReconcilable, needsReconcile, reconcileActionLabel, reconcileOutcome,
  reconcilePlan, syncSummary,
} from '@/lib/git'
import { gitBranchTone, reconcileTone, syncTone } from '@/lib/statusTone'
import type {
  GitAheadBehind, GitBranch, GitLogEntry, GitSync, GitView as GitViewData, Project,
} from '@/lib/schemas'

type GitView = 'historique' | 'fichiers' | 'diff'
type Search = { project?: string; view?: GitView; sha?: string }

const VIEWS = [
  { value: 'historique', label: 'Historique' },
  { value: 'fichiers', label: 'Fichiers' },
  { value: 'diff', label: 'Diff' },
] as const satisfies ReadonlyArray<{ value: GitView; label: string }>

// Dernier projet consulté — défaut malin quand on atterrit sur `/git` nu (git est per-projet). Persisté en
// localStorage (patron `useRailCollapse`) ; dégrade silencieusement (mode privé / quota) vers « pas de défaut ».
const LAST_PROJECT_KEY = 'forgemaster.git.lastProject'

const readLastProject = (): string | null => {
  try {
    return localStorage.getItem(LAST_PROJECT_KEY)
  } catch {
    return null
  }
}

/** Surface **Git de plein droit** — atteinte depuis le rail (route GLOBALE `/git`), pilotée par l'URL
 *  (`?project=<slug>&view=<vue>&sha=<commit>`) → deep-linkable, capturable at-rest. Git étant **per-projet**
 *  (l'API est `/api/projects/{project}/git`), un **sélecteur de projet** surmonte le SoT read-only : en-tête
 *  (réfs + SHA + synchro miroir + réconciliation ff-only consent-gated) puis segmented
 *  **[Historique · Fichiers · Diff]** — une vue à la fois. **Ex-drawer Ops**, promu en destination : un seul
 *  organisateur git (le drawer `?panel=git` est retiré, plus deux endroits pour la même donnée). */
export function GitExplorer() {
  const search = useSearch({ strict: false }) as Search
  const navigate = useNavigate()
  const projects = useProjects()

  const setProject = (slug: string) => {
    try {
      if (slug) localStorage.setItem(LAST_PROJECT_KEY, slug)
    } catch {
      /* stockage indisponible (mode privé / quota) — le choix reste porté par l'URL */
    }
    navigate({ to: '/git', search: () => (slug ? { project: slug } : {}) })
  }

  // Défaut malin : sur `/git` nu, reprendre le dernier projet consulté s'il existe ENCORE (jamais un projet
  // supprimé). `replace` pour ne pas polluer l'historique. Une fois `?project` posé, la garde coupe l'effet.
  useEffect(() => {
    if (search.project || projects.isPending || projects.isError) return
    const last = readLastProject()
    if (last && (projects.data ?? []).some((p) => p.slug === last)) {
      navigate({ to: '/git', replace: true, search: () => ({ project: last }) })
    }
  }, [search.project, projects.data, projects.isPending, projects.isError, navigate])

  const project = search.project ?? ''

  return (
    <div className="space-y-5">
      <GitHeader
        options={projects.data ?? []}
        loading={projects.isPending}
        error={projects.isError ? projects.error : null}
        project={project}
        onProject={setProject}
      />
      {!project ? (
        <EmptyState
          title="Choisis un projet"
          description="Le dépôt Git est propre à un projet. Sélectionne-en un ci-dessus pour parcourir ses branches, son historique, son arbre de fichiers et ses diffs."
        />
      ) : (
        <GitSurface key={project} project={project} view={search.view ?? 'historique'} sha={search.sha} />
      )}
    </div>
  )
}

/** L'en-tête de la surface : titre + **sélecteur de projet** (le contexte « quel dépôt »), toujours visible —
 *  c'est le commutateur de repo. Daemon injoignable → Alert honnête à la place du sélecteur. */
function GitHeader({ options, loading, error, project, onProject }: {
  options: Project[]
  loading: boolean
  error: unknown
  project: string
  onProject: (slug: string) => void
}) {
  return (
    <div className="space-y-3">
      <SectionTitle eyebrow="Dépôt du projet" title="Git" />
      {error ? (
        <Alert tone="danger" title="Projets indisponibles">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      ) : (
        <Select
          aria-label="Projet"
          value={project}
          onChange={(e) => onProject(e.target.value)}
          disabled={loading}
        >
          <option value="">— choisir un projet —</option>
          {options.map((p) => (
            <option key={p.id} value={p.slug}>{p.slug}</option>
          ))}
        </Select>
      )}
    </div>
  )
}

/** Le corps de la surface pour un projet donné (ex-`GitPanel`) : en-tête compact (réfs + SHA de tête + synchro
 *  dev↔main + synchro miroir + réconciliation ff-only) surmontant le segmented **[Historique · Fichiers · Diff]**.
 *  `view`/`sha` viennent de l'URL (deep-linkables) ; les callbacks les réécrivent en préservant `?project`.
 *  Aucune mutation hormis la réconciliation (consent-gated). */
function GitSurface({ project, view, sha }: { project: string; view: GitView; sha?: string }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useGit(project)
  // Sync miroir : RÉSEAU, manuel — jamais auto (enabled:false) ; le refresh manuel déclenche les DEUX
  // (vue read-only idempotente + fetch du miroir), pour que le badge reflète l'état après un clic. Le cache
  // (même queryKey) est aussi lu par le rail pour son dot rollup → on le garde chaud via ce double refetch.
  const sync = useGitSync(project)
  const onRefresh = () => { void refetch(); void sync.refetch() }
  const [reconcileOpen, setReconcileOpen] = useState(false)  // panneau de réconciliation ff-only déplié
  const navigate = useNavigate()

  // Sous-vues deep-linkables : `?view=` (segment) et `?sha=` (détail commit), en préservant `?project`.
  const setView = (v: GitView) =>
    navigate({ to: '/git', search: (prev) => ({ ...prev, view: v, sha: undefined }) })
  const setSha = (s: string | null) =>
    navigate({ to: '/git', search: (prev) => ({ ...prev, sha: s ?? undefined }) })

  if (isLoading) return <div className="py-6"><LoadingState label="Lecture du dépôt…" /></div>
  if (isError || !data) {
    return (
      <div className="space-y-3 py-6">
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
  // Tête du dépôt : la réf `dev` (sinon la 1ʳᵉ branche) — l'ancre « où pointe le dépôt » de l'en-tête.
  const headRef = data.branches.find((b) => b.name === 'dev') ?? data.branches[0]
  // Branche représentante de la paire protégée (dev/main) pour la rangée unifiée quand dev==main.
  const unifiedHead = data.branches.find((b) => refs.includes(b.name))

  return (
    <div className="space-y-4">
      {/* En-tête HIÉRARCHISÉ : la TÊTE (réf + SHA) prime, ancrée à gauche ; puis les signaux de synchro
          groupés à part ; l'énumération complète des branches vit dans l'organisateur ci-dessous (pas ici,
          pour ne pas noyer le signal de tête ni murer le mobile). */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {headRef && (
          <span className="flex items-center gap-2">
            <Badge tone={gitBranchTone(headRef.name)}>{headRef.name}</Badge>
            <code className="font-mono text-xs text-muted">{headRef.sha}</code>
          </span>
        )}
        <span className="flex flex-wrap items-center gap-2">
          {data.ahead_behind && <SyncChip ab={data.ahead_behind} />}
          <RemoteSyncChip sync={sync} />
          {sync.data && needsReconcile(sync.data) && (
            <Button variant="ghost" size="sm" onClick={() => setReconcileOpen((o) => !o)}
              aria-expanded={reconcileOpen}>⟳ Réconcilier</Button>
          )}
        </span>
        <RefreshButton className="ml-auto" onClick={onRefresh} busy={isFetching || sync.isFetching} />
      </div>

      {/* Réconciliation ff-only : preview (dérivée de l'état de sync) → confirme → exécute → re-fetch le badge.
          Repliée par défaut, ouverte par le bouton « ⟳ Réconcilier ». */}
      {reconcileOpen && sync.data && (
        <ReconcilePanel project={project} sync={sync.data}
          onDone={() => { void sync.refetch() }} onClose={() => setReconcileOpen(false)} />
      )}

      {/* Config miroir/token = réglage, pas lecture → repliée par défaut pour rendre la hauteur au dépôt. */}
      <Collapsible title="Miroir GitHub & token de push">
        <ProjectCredentialCard project={project} bare />
      </Collapsible>

      <Segmented ariaLabel="Vue Git" options={VIEWS} value={view} onChange={setView} />

      {view === 'historique' && (
        <div className="space-y-4">
          {sha && <CommitDetailCard project={project} sha={sha} onClose={() => setSha(null)} />}
          {/* UN organisateur unifié : les branches en index ; dev/main DÉPLIENT leur log inline (accordéon),
              les features ouvrent le détail de leur tête. Fini la carte « Branches » doublée de LogCards
              demi-largeur qui répétaient les mêmes réfs et laissaient le canvas à moitié vide (axe 5/4). */}
          <Card className="p-2">
            {hasBranches ? (
              <ul className="divide-y divide-border/50">
                {buildBranchRows(data, refs, unified, unifiedHead).map((row, i) => (
                  <BranchRow key={row.branch.name} branch={row.branch} also={row.also} log={row.log}
                    defaultOpen={i === 0} onSelect={setSha} />
                ))}
              </ul>
            ) : (
              <EmptyState title="Aucune branche" description="Le SoT ne porte encore aucune branche." />
            )}
          </Card>
        </div>
      )}

      {view === 'fichiers' && (
        hasBranches ? (
          <RepoExplorer project={project} branches={data.branches} tags={data.tags} />
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

/** Panneau de réconciliation **ff-only** (la seule mutation git de l'UI). **Preview d'abord** : le plan est
 *  DÉRIVÉ de l'état de sync (source unique = l'`state` par branche du GET `/git/sync`), jamais un dry-run POST.
 *  Confirme → POST `reconcile` (backend ff-only : jamais de merge non-ff) → affiche le résultat par branche →
 *  re-fetch le badge. Un état sans branche ff-able (tout divergé) n'offre pas de bouton d'exécution : il
 *  EXPLIQUE que la résolution est manuelle (spec forge-sot-local). */
function ReconcilePanel({ project, sync, onDone, onClose }: {
  project: string
  sync: GitSync
  onDone: () => void
  onClose: () => void
}) {
  const reconcile = useReconcileSync(project)
  const plan = reconcilePlan(sync)
  const actionable = isReconcilable(sync)
  const report = reconcile.data
  return (
    <Card className="space-y-3 border-warn-500/30 p-5">
      <div className="flex items-center gap-2">
        <p className="text-sm font-medium text-fg">Réconciliation miroir (fast-forward uniquement)</p>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={onClose}>Fermer</Button>
      </div>

      {/* Preview : ce que la réconciliation FERAIT, par branche (dérivé de l'état, pas d'appel réseau). */}
      {!report && (
        <>
          <ul className="space-y-1.5">
            {plan.map((p) => (
              <li key={p.branch} className="flex items-center gap-2 text-sm">
                <Badge tone={gitBranchTone(p.branch)}>{p.branch}</Badge>
                <span className="text-muted">→</span>
                <Badge tone={syncTone(p.state)} dot>{p.label}</Badge>
              </li>
            ))}
          </ul>
          {actionable ? (
            <div className="flex items-center gap-2">
              <Button variant="primary" size="sm" busy={reconcile.isPending}
                onClick={() => reconcile.mutate(undefined, { onSuccess: onDone })}>
                Confirmer (ff-only)
              </Button>
              <span className="text-xs text-faint">Aucun merge non-ff : les branches divergées sont laissées.</span>
            </div>
          ) : (
            <Alert tone="danger" title="Divergence non fast-forward">
              Aucune branche n'est réconciliable automatiquement (divergence réelle SoT↔GitHub). Résous le
              conflit à la main — le forgemaster ne merge jamais de non-ff (le SoT reste autoritaire).
            </Alert>
          )}
        </>
      )}

      {/* Erreur d'exécution (ex. SoT corrompu) : honnête, jamais un demi-succès silencieux. */}
      {reconcile.isError && (
        <Alert tone="danger" title="Réconciliation impossible">
          {reconcile.error instanceof ApiError ? reconcile.error.detail : String(reconcile.error)}
        </Alert>
      )}

      {/* Résultat : l'action RÉELLEMENT appliquée par branche (fetch frais côté backend fait autorité). */}
      {report && (
        <>
          <div className="flex items-center gap-2">
            <Badge tone={report.blocked.length ? 'warn' : 'ok'} dot>{reconcileOutcome(report)}</Badge>
          </div>
          <ul className="space-y-1.5">
            {Object.entries(report.actions).map(([branch, a]) => (
              <li key={branch} className="flex items-center gap-2 text-sm">
                <Badge tone={gitBranchTone(branch)}>{branch}</Badge>
                <span className="text-muted">→</span>
                <Badge tone={reconcileTone(a.action)} dot>{reconcileActionLabel(a.action)}</Badge>
                {a.reason && <span className="text-xs text-faint" title={a.reason}>{a.reason}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  )
}

type BranchRowModel = { branch: GitBranch; also?: string[]; log?: GitLogEntry[] }

/** Ordonne les rangées de l'organisateur unifié : les réfs protégées d'abord (dev/main, porteuses d'un log
 *  dépliable), les features ensuite (sans log → clic direct sur la tête). dev==main : UNE rangée pour la
 *  paire (badges cumulés + log partagé), au lieu de deux lignes identiques (axe 5). */
function buildBranchRows(
  data: GitViewData, refs: string[], unified: boolean, unifiedHead: GitBranch | undefined,
): BranchRowModel[] {
  const features = data.branches.filter((b) => !refs.includes(b.name))
  const protectedRows: BranchRowModel[] = unified && unifiedHead
    ? [{ branch: unifiedHead, also: refs.filter((r) => r !== unifiedHead.name), log: data.logs[unifiedHead.name] }]
    : refs
        .map((r) => data.branches.find((b) => b.name === r))
        .filter((b): b is GitBranch => Boolean(b))
        .map((b) => ({ branch: b, log: data.logs[b.name] }))
  return [...protectedRows, ...features.map((b) => ({ branch: b }))]
}

/** Une rangée de branche dans l'organisateur unifié. **Tissu** (ghost, relief au hover) + **scent de
 *  cliquabilité** à rest = un caret `▸` (idiome `Collapsible`, rotate au dépli). dev/main portent un `log`
 *  → la rangée DÉPLIE son log inline (accordéon) ; une feature (sans log) ouvre directement le détail de sa
 *  tête (`?sha`). Toujours via la primitive Button (jamais un `<button>` brut — R1). `also` (mode unifié
 *  dev==main) : réfs supplémentaires pointant le MÊME commit, rendues en badges + « identiques » (axe 5). */
function BranchRow({ branch, also, log, defaultOpen, onSelect }: {
  branch: GitBranch
  also?: string[]
  log?: GitLogEntry[]
  defaultOpen?: boolean
  onSelect: (sha: string) => void
}) {
  const hasLog = (log?.length ?? 0) > 0
  const [open, setOpen] = useState(Boolean(defaultOpen && hasLog))
  return (
    <li>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => (hasLog ? setOpen((o) => !o) : onSelect(branch.sha))}
        aria-expanded={hasLog ? open : undefined}
        className="w-full justify-start gap-3"
      >
        {/* Scent : caret de dépli (tissu, cohérent Collapsible) — colonne de LARGEUR FIXE (masqué sans log)
            pour que tous les badges de réf s'alignent, expandable comme feuille. */}
        <span aria-hidden className={cn('w-3 shrink-0 text-center text-faint transition-transform',
          hasLog && open && 'rotate-90', !hasLog && 'opacity-0')}>▸</span>
        <span className="flex shrink-0 items-center gap-1.5">
          <Badge tone={gitBranchTone(branch.name)}>{branch.name}</Badge>
          {also?.map((n) => <Badge key={n} tone={gitBranchTone(n)}>{n}</Badge>)}
          {also && also.length > 0 && <span className="text-xs text-faint">identiques</span>}
        </span>
        {/* sha+sujet = résumé de tête, UNIQUEMENT à l'état replié (ou feuille) : déplié, le log porte déjà
            ce commit en 1ʳᵉ ligne → ne pas le rebégayer dans l'en-tête (axe 5). */}
        {!open && (
          <>
            <code className="shrink-0 font-mono text-xs text-muted">{branch.sha}</code>
            <span className="truncate text-sm text-muted" title={branch.subject}>{branch.subject}</span>
          </>
        )}
      </Button>
      {hasLog && open && (
        <ol className="ml-6 space-y-0.5 border-l border-border/60 py-1 pl-3">
          {log!.map((e) => (
            <li key={e.sha}>
              <Button variant="ghost" size="sm" onClick={() => onSelect(e.sha)}
                className="w-full items-baseline justify-start gap-2">
                <code className="shrink-0 font-mono text-xs text-muted">{e.sha}</code>
                <span className="truncate text-sm text-fg" title={e.subject}>{e.subject}</span>
              </Button>
            </li>
          ))}
        </ol>
      )}
    </li>
  )
}
