import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useSearch } from '@tanstack/react-router'
import { Alert, Badge, Button, Card, EmptyState, LoadingState, RefreshButton, SectionTitle } from '@/components/ui'
import { Transcript } from '@/components/dispatch/Transcript'
import { ApiError } from '@/lib/api'
import { useDispatch, useFeatureJobs, useJob, useRoadmap } from '@/lib/queries'
import { useDispatchStream } from '@/lib/useDispatchStream'
import { JOB_STATUS_TONE, TASK_STATE_TONE, toneFor } from '@/lib/statusTone'
import { jobStatusLabel, stateLabel } from '@/lib/taskLabels'
import type { FeatureWithTasks, Job, JobFrame, TranscriptEvent } from '@/lib/schemas'

const RUNNING = new Set(['running', 'pending'])
type Ev = Exclude<TranscriptEvent, JobFrame>

/** Onglet Dispatch : choisit une feature, dispatche sa NEXT task (POST long bloquant), DÉCOUVRE le job en
 *  cours (le job_id n'arrive qu'à la fin du POST) et streame son transcript live via WS. */
export function DispatchTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { feature: deepFeature } = useSearch({ strict: false }) as { feature?: string }
  const roadmap = useRoadmap(project)

  const features = roadmap.data?.features ?? []
  const [selected, setSelected] = useState<string | null>(null)
  // Feature effective : sélection explicite → deep-link → 1ʳᵉ feature dispatchable → 1ʳᵉ feature.
  const active = selected ?? deepFeature ?? features.find((f) => f.next)?.slug ?? features[0]?.slug ?? null
  const feature = features.find((f) => f.slug === active) ?? null

  if (roadmap.isLoading) return <div className="p-8"><LoadingState label="Chargement des features…" /></div>
  if (roadmap.isError) {
    return (
      <div className="space-y-3 p-8">
        <Alert tone="danger" title="Roadmap indisponible">
          {roadmap.error instanceof ApiError ? roadmap.error.detail : String(roadmap.error)}
        </Alert>
        <RefreshButton onClick={() => roadmap.refetch()} busy={roadmap.isFetching} />
      </div>
    )
  }
  if (features.length === 0) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          title="Aucune feature à dispatcher"
          description="Ajoute une feature et ses tasks (CLI cockpit ou API) pour dispatcher un worker sur sa NEXT task."
        />
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      <SectionTitle
        eyebrow="worker"
        title="Dispatch"
        actions={<RefreshButton onClick={() => roadmap.refetch()} busy={roadmap.isFetching} />}
      />
      <div className="flex flex-wrap gap-1.5">
        {features.map((f) => (
          <Button
            key={f.id}
            size="sm"
            variant={f.slug === active ? 'primary' : 'secondary'}
            onClick={() => setSelected(f.slug)}
          >
            {f.title ?? f.slug}
            {f.next && <span className="ml-1.5 opacity-70">· prêt</span>}
          </Button>
        ))}
      </div>
      {feature && <DispatchPanel key={feature.id} project={project} feature={feature} />}
    </div>
  )
}

/** Panneau d'une feature : NEXT task, bouton de dispatch, transcript du job sélectionné, historique.
 *  Un run EN COURS est streamé en live (WS) ; un run TERMINÉ est lu at-rest par HTTP (pas de socket ouvert). */
function DispatchPanel({ project, feature }: { project: string; feature: FeatureWithTasks }) {
  const dispatch = useDispatch(project, feature.slug)
  const jobs = useFeatureJobs(project, feature.slug, dispatch.isPending ? 1000 : false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const armedBaseline = useRef<string | null | undefined>(undefined)

  const jobList = useMemo(() => jobs.data ?? [], [jobs.data])
  const nextTask = feature.next ? feature.tasks.find((t) => t.slug === feature.next) : undefined

  const selectedJob = jobList.find((j) => j.id === activeJobId) ?? null
  const running = Boolean(selectedJob && RUNNING.has(selectedJob.status))
  const stream = useDispatchStream(running ? activeJobId : null) // WS : live d'un run en cours seulement
  const detail = useJob(!running ? activeJobId : null) // HTTP : transcript at-rest d'un run terminé

  // Sélection du job à streamer. Après un dispatch : dès qu'un job PLUS RÉCENT que la baseline (capturée au
  // clic) apparaît, on le streame. Sinon (au chargement), on affiche le run le plus récent — sans écraser une
  // sélection manuelle de l'historique (setState fonctionnel `cur ?? top.id`).
  useEffect(() => {
    const top = jobList[0]
    if (!top) return
    if (armedBaseline.current !== undefined) {
      if (top.id !== armedBaseline.current) {
        setActiveJobId(top.id)
        armedBaseline.current = undefined
      }
      return
    }
    setActiveJobId((cur) => cur ?? top.id)
  }, [jobList])

  function onDispatch() {
    armedBaseline.current = jobList[0]?.id ?? null // découvrira le job créé par ce dispatch
    dispatch.mutate()
  }

  return (
    <Card className="space-y-5 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <p className="text-sm text-muted">Prochaine task dispatchable</p>
          {nextTask ? (
            <div className="flex items-center gap-2">
              <span className="font-medium text-fg">{nextTask.title ?? nextTask.slug}</span>
              <Badge tone={toneFor(TASK_STATE_TONE, nextTask.state)} dot>
                {stateLabel(nextTask.state)}
              </Badge>
            </div>
          ) : (
            <p className="text-sm text-faint">Aucune task READY — rien à dispatcher.</p>
          )}
        </div>
        <Button variant="primary" onClick={onDispatch} busy={dispatch.isPending} disabled={!feature.next}>
          {dispatch.isPending ? 'Dispatch en cours…' : 'Dispatcher la NEXT task'}
        </Button>
      </div>

      {dispatch.isError && (
        <Alert tone="danger" title="Échec du dispatch">
          {dispatch.error instanceof ApiError ? dispatch.error.detail : String(dispatch.error)}
        </Alert>
      )}
      {dispatch.data && !dispatch.data.dispatched && (
        <Alert tone="warn" title="Dispatch refusé">{dispatch.data.reason}</Alert>
      )}

      {activeJobId && (
        <TranscriptPanel
          jobId={activeJobId}
          events={
            running ? stream.events : (detail.data?.events ?? []).filter((e): e is Ev => e.type !== 'job')
          }
          live={running && (stream.status === 'open' || stream.status === 'connecting')}
          loading={!running && detail.isLoading}
          status={running ? (stream.terminal?.status ?? selectedJob?.status ?? 'running') : (selectedJob?.status ?? 'done')}
          finished={running ? Boolean(stream.terminal) : true}
          numTurns={running ? stream.terminal?.num_turns : detail.data?.job.num_turns}
          costUsd={running ? stream.terminal?.cost_usd : detail.data?.job.cost_usd}
        />
      )}

      {jobList.length > 0 && (
        <JobHistory jobs={jobList} activeJobId={activeJobId} onSelect={setActiveJobId} />
      )}
    </Card>
  )
}

/** Transcript d'un job — live (WS, badge « live ») ou at-rest (HTTP, badge du statut). Rendu identique :
 *  le contrat d'événement est unique (jobs.normalize_line), seule la SOURCE diffère selon que le run tourne. */
function TranscriptPanel({
  jobId,
  events,
  live,
  loading,
  status,
  finished,
  numTurns,
  costUsd,
}: {
  jobId: string
  events: Ev[]
  live: boolean
  loading: boolean
  status: string
  finished: boolean
  numTurns?: number | null
  costUsd?: number | null
}) {
  return (
    <div className="space-y-3 border-t border-border pt-4">
      <div className="flex items-center justify-between gap-3">
        <SectionTitle eyebrow={`job ${jobId.slice(0, 8)}`} title="Transcript" />
        <Badge tone={live ? 'info' : toneFor(JOB_STATUS_TONE, status)} dot>
          {live ? 'live' : jobStatusLabel(status)}
        </Badge>
      </div>
      {loading ? (
        <LoadingState label="Chargement du transcript…" />
      ) : events.length === 0 ? (
        <p className="text-sm text-faint">
          {live ? 'En attente des premiers événements du worker…' : 'Aucun événement dans ce transcript.'}
        </p>
      ) : (
        <Transcript events={events} />
      )}
      {finished && (
        <p className="text-xs text-faint">
          Run {jobStatusLabel(status)}
          {numTurns != null && ` · ${numTurns} tours`}
          {costUsd != null && ` · $${costUsd.toFixed(4)}`}
        </p>
      )}
    </div>
  )
}

function JobHistory({
  jobs,
  activeJobId,
  onSelect,
}: {
  jobs: Job[]
  activeJobId: string | null
  onSelect: (id: string) => void
}) {
  return (
    <div className="space-y-2 border-t border-border pt-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-faint">Historique des runs</p>
      <ul className="space-y-1">
        {jobs.map((j) => (
          <li key={j.id}>
            <Button
              size="sm"
              variant={j.id === activeJobId ? 'secondary' : 'ghost'}
              className="w-full justify-between"
              onClick={() => onSelect(j.id)}
            >
              <span className="font-mono text-xs">{j.id.slice(0, 8)}</span>
              <span className="flex items-center gap-2">
                {j.task_slug && <span className="text-muted">{j.task_slug}</span>}
                <Badge tone={toneFor(JOB_STATUS_TONE, j.status)} dot>
                  {jobStatusLabel(j.status)}
                </Badge>
              </span>
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )
}
