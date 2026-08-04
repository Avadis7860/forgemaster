import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Alert, Badge, Button, Card, Dialog, LoadingState, SectionTitle } from '@/components/ui'
import { Transcript } from '@/components/dispatch/Transcript'
import { ClaudeAuthBlock } from '@/components/ClaudeAuthStatus'
import { ApiError } from '@/lib/api'
import { useAbort, useDispatch, useFeatureJobs, useJob, useOnboarding } from '@/lib/queries'
import { useDispatchStream } from '@/lib/useDispatchStream'
import { JOB_KIND_TONE, JOB_STATUS_TONE, TASK_STATE_TONE, toneFor } from '@/lib/statusTone'
import { jobKindLabel, jobStatusLabel, stateLabel } from '@/lib/taskLabels'
import type { FeatureWithTasks, Job, JobFrame, TranscriptEvent } from '@/lib/schemas'

const RUNNING = new Set(['running', 'pending'])
type Ev = Exclude<TranscriptEvent, JobFrame>

/** Panneau d'une feature : NEXT task, bouton de dispatch, transcript du job sélectionné, historique.
 *  Un run EN COURS est streamé en live (WS) ; un run TERMINÉ est lu at-rest par HTTP (pas de socket ouvert).
 *  Extrait de l'ex-onglet Dispatch pour être composé dans la surface « Travail » (fusion Dispatch+Gate). */
export function DispatchPanel({ project, feature }: { project: string; feature: FeatureWithTasks }) {
  const onboarding = useOnboarding()
  const claudeAuth = onboarding.data?.claude_auth
  // Gate visible AVANT le clic : sans auth Claude, le POST /dispatch renverrait 403 (worker jamais spawné) →
  // on l'annonce et on désarme le bouton plutôt que d'attendre l'erreur. `undefined` (onboarding pas chargé)
  // ne bloque pas — le backend reste l'autorité (fail-closed côté serveur).
  const authBlocked = claudeAuth ? !claudeAuth.authenticated : false
  const navigate = useNavigate()
  const dispatch = useDispatch(project, feature.slug)
  const abort = useAbort(project, feature.slug)
  const [confirmAbort, setConfirmAbort] = useState(false)
  const jobs = useFeatureJobs(project, feature.slug, dispatch.isPending ? 1000 : false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const armedBaseline = useRef<string | null | undefined>(undefined)

  const jobList = useMemo(() => jobs.data ?? [], [jobs.data])
  const nextTask = feature.next ? feature.tasks.find((t) => t.slug === feature.next) : undefined
  // Next task INTERACTIVE (interview de socle) : ne se dispatche pas en headless → l'action primaire n'est PAS
  // « Dispatcher » (un no-op ici) mais « Lancer l'interview », qui ouvre l'onglet Ops en auto-lançant la
  // commande (search param `run=interview` relayé au TerminalPane). Détecté depuis la roadmap, AVANT tout clic.
  const interactiveNext = nextTask?.mode === 'interactive'
  const launchInterview = () =>
    navigate({ to: '/$project/ops', params: { project }, search: { run: 'interview' } })

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
        <div className="flex items-center gap-2">
          {/* Arrêt de première classe : visible UNIQUEMENT tant qu'un run est actif (POST dispatch bloquant OU
              job en vol). Gardé par une confirmation (l'abort tue des process). L'abort débloque le POST
              dispatch en cours → il rend un rapport `aborted`. */}
          {(dispatch.isPending || running) && (
            <Button variant="danger" onClick={() => setConfirmAbort(true)} busy={abort.isPending}>
              Arrêter le run
            </Button>
          )}
          {interactiveNext ? (
            <Button variant="primary" onClick={launchInterview}>
              Lancer l'interview dans le terminal
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={onDispatch}
              busy={dispatch.isPending}
              disabled={!feature.next || authBlocked}
            >
              {dispatch.isPending ? 'Dispatch en cours…' : 'Dispatcher la feature'}
            </Button>
          )}
        </div>
      </div>

      {/* Confirmation d'abort : l'acte tue les workers en cours (irréversible pour le run courant) → garde
          explicite (patron `LeaveTerminalConfirm`). Rien n'est mergé, la task revient `todo`, run relançable. */}
      <Dialog open={confirmAbort} onOpenChange={setConfirmAbort} side="center" title="Arrêter le run ?">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-fg">
            Les workers en cours seront arrêtés (leurs process tués) et le mutex de la feature libéré.
            <strong> Rien ne sera mergé</strong> — la task revient à l'état « à faire » et le run est
            relançable.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setConfirmAbort(false)}>
              Annuler
            </Button>
            <Button
              variant="danger"
              size="sm"
              busy={abort.isPending}
              onClick={() => {
                abort.mutate()
                setConfirmAbort(false)
              }}
            >
              Arrêter le run
            </Button>
          </div>
        </div>
      </Dialog>

      {authBlocked && claudeAuth && <ClaudeAuthBlock auth={claudeAuth} />}

      {dispatch.isError && (
        <Alert tone="danger" title="Échec du dispatch">
          {dispatch.error instanceof ApiError ? dispatch.error.detail : String(dispatch.error)}
        </Alert>
      )}
      {dispatch.data && dispatch.data.dispatched === 0 && dispatch.data.finalizations.length === 0 && (
        <Alert tone="warn" title="Rien à drainer">Aucune task READY dans cette feature.</Alert>
      )}
      {dispatch.data && dispatch.data.failed > 0 && !dispatch.data.aborted && (
        <Alert tone="danger" title="Dispatch en échec">
          {dispatch.data.runs.find((r) => !r.ok)?.reason ?? 'un run a échoué'}
        </Alert>
      )}
      {dispatch.data?.aborted && (
        <Alert tone="warn" title="Run interrompu">
          Run arrêté — workers stoppés, mutex libéré, rien mergé. Relançable.
        </Alert>
      )}
      {abort.isError && (
        <Alert tone="danger" title="Échec de l'arrêt">
          {abort.error instanceof ApiError ? abort.error.detail : String(abort.error)}
        </Alert>
      )}
      {/* Socle interactif : une interview ne se mène pas en `claude -p` → l'action primaire (en-tête) ouvre
          l'onglet Ops en auto-lançant `forgemaster interview <projet>` (search param `run=interview` → TerminalPane).
          Ce bloc explicite POURQUOI le bouton diffère — le point d'entrée UI que le dispatch muet n'offrait pas. */}
      {interactiveNext && (
        <Alert tone="info" title="Interview de 1ʳᵉ session requise">
          Le socle de ce projet se cadre en <strong>terminal interactif</strong> (pas en headless) : lance
          l'interview ci-dessus — un humain mène la conception, puis la roadmap de travail en est dérivée.
        </Alert>
      )}

      {/* Transcript (gauche, resserré) + historique (colonne droite) côte à côte sur large ; empilés sur
          étroit (grid-cols-1 par défaut). `min-w-0` : sans quoi une ligne de transcript longue force la
          colonne à déborder au lieu de scroller (min-width:auto par défaut d'un enfant de grille). */}
      {jobList.length > 0 && (
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="min-w-0 lg:col-span-2">
            {activeJobId && (
              <TranscriptPanel
                jobId={activeJobId}
                kind={selectedJob?.kind}
                events={
                  running ? stream.events : (detail.data?.events ?? []).filter((e): e is Ev => e.type !== 'job')
                }
                live={running && (stream.status === 'open' || stream.status === 'connecting')}
                loading={!running && detail.isLoading}
                status={running ? (stream.terminal?.status ?? selectedJob?.status ?? 'running') : (selectedJob?.status ?? 'done')}
                finished={running ? Boolean(stream.terminal) : true}
                numTurns={running ? stream.terminal?.num_turns : detail.data?.job.num_turns}
                costUsd={running ? stream.terminal?.cost_usd : detail.data?.job.cost_usd}
                error={running ? undefined : (detail.data?.job.error ?? selectedJob?.error)}
              />
            )}
          </div>
          <div className="min-w-0 lg:col-span-1">
            <JobHistory jobs={jobList} activeJobId={activeJobId} onSelect={setActiveJobId} />
          </div>
        </div>
      )}
    </Card>
  )
}

/** Transcript d'un job — live (WS, badge « live ») ou at-rest (HTTP, badge du statut). Rendu identique :
 *  le contrat d'événement est unique (jobs.normalize_line), seule la SOURCE diffère selon que le run tourne. */
function TranscriptPanel({
  jobId,
  kind,
  events,
  live,
  loading,
  status,
  finished,
  numTurns,
  costUsd,
  error,
}: {
  jobId: string
  kind?: string | null
  events: Ev[]
  live: boolean
  loading: boolean
  status: string
  finished: boolean
  numTurns?: number | null
  costUsd?: number | null
  error?: string | null
}) {
  // Eyebrow honnête : un run non-task (review/toolchain/fix) est nommé par son GENRE, pas « job », pour
  // qu'on sache CE qu'on lit (le reviewer-de-gate n'est pas la task qu'il ancre).
  const label = kind && kind !== 'task' ? jobKindLabel(kind) : 'job'
  return (
    <div className="space-y-3 border-t border-border pt-4">
      <div className="flex items-center justify-between gap-3">
        <SectionTitle eyebrow={`${label} ${jobId.slice(0, 8)}`} title="Transcript" />
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
      {error && (
        <Alert tone="danger" title="Raison de l'échec">
          <span className="font-mono text-xs">{error}</span>
        </Alert>
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
                {j.kind && j.kind !== 'task' ? (
                  <Badge tone={toneFor(JOB_KIND_TONE, j.kind)}>{jobKindLabel(j.kind)}</Badge>
                ) : (
                  j.task_slug && <span className="text-muted">{j.task_slug}</span>
                )}
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
