import { useState } from 'react'
import { useParams } from '@tanstack/react-router'
import { Alert, Badge, Button, Card, EmptyState, LoadingState, RefreshButton, Segmented } from '@/components/ui'
import { ApiError } from '@/lib/api'
import {
  useDeploy, useDeploymentLogs, useDeployments, useReconcileDeployments, useRestartDeployment,
  useStopDeployment,
} from '@/lib/queries'
import { deploymentTone, gitBranchTone } from '@/lib/statusTone'
import type { Deployment } from '@/lib/schemas'

// Libellé FR par état de run (source unique de statut = le backend ; l'UI ne fait que traduire + teinter).
const STATUS_LABEL: Record<string, string> = {
  no_deploy: 'jamais déployé',
  building: 'build en cours',
  running: 'en marche',
  stopped: 'arrêté',
  unhealthy: 'en panne',
}

type TailOpt = '100' | '500'
const TAILS = [
  { value: '100', label: '100 lignes' },
  { value: '500', label: '500 lignes' },
] as const satisfies ReadonlyArray<{ value: TailOpt; label: string }>

/** Onglet Runtime (P5) : observabilité des 2 déploiements (`main`/`dev`) d'un projet. Une carte par
 *  déploiement — santé (état de run teinté, jamais un faux-vert), port + sha, **lien health-gated** (actif
 *  seulement si le service répond), lifecycle (deploy/stop/restart) et un log viewer borné à la demande. Le
 *  RefreshButton global réconcilie l'état LIVE (`compose ps`) — jamais en poll (calque du GitTab). */
export function RuntimeTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { data, isLoading, isError, error, refetch, isFetching } = useDeployments(project)
  const reconcile = useReconcileDeployments(project)
  // Le refresh relit la liste ET réconcilie le live des 2 branches (à la main, jamais auto — comme /git/sync).
  const onRefresh = () => { void refetch(); reconcile.mutate() }

  if (isLoading) return <div className="p-8"><LoadingState label="Lecture des déploiements…" /></div>
  if (isError || !data) {
    return (
      <div className="space-y-3 p-8">
        <Alert tone="danger" title="Vue Runtime indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
        <RefreshButton onClick={onRefresh} busy={isFetching} />
      </div>
    )
  }

  return (
    <div className="space-y-4 p-6">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium text-fg">Déploiements</p>
        <span className="text-xs text-faint">santé · logs · liens — 1 conteneur compose isolé par projet</span>
        <RefreshButton className="ml-auto" onClick={onRefresh} busy={isFetching || reconcile.isPending} />
      </div>
      <div className="space-y-4">
        {data.deployments.map((dep) => (
          <DeploymentCard key={dep.branch} project={project} dep={dep} />
        ))}
      </div>
    </div>
  )
}

/** Une carte de déploiement : santé + port/sha + lien gaté, actions de lifecycle, log viewer repliable.
 *  Les hooks de mutation vivent au niveau de la carte (une carte = une instance stable). */
function DeploymentCard({ project, dep }: { project: string; dep: Deployment }) {
  const deploy = useDeploy(project, dep.branch)
  const stop = useStopDeployment(project, dep.branch)
  const restart = useRestartDeployment(project, dep.branch)
  const [logsOpen, setLogsOpen] = useState(false)

  const busy = deploy.isPending || stop.isPending || restart.isPending
  const live = dep.status === 'running'
  const mounted = dep.status !== 'no_deploy'          // jamais monté → stop/restart/logs sans objet
  const actionError = deploy.error ?? stop.error ?? restart.error

  return (
    <Card className="space-y-3 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={gitBranchTone(dep.branch)}>{dep.branch}</Badge>
        <Badge tone={deploymentTone(dep.status)} dot>{STATUS_LABEL[dep.status] ?? dep.status}</Badge>
        {dep.port != null && <code className="font-mono text-xs text-faint">:{dep.port}</code>}
        {dep.last_deploy_sha && (
          <code className="font-mono text-xs text-faint" title="sha du dernier deploy">
            {dep.last_deploy_sha.slice(0, 8)}
          </code>
        )}
        {/* Lien health-gated : actif seulement si le service RÉPOND (running) — sinon inerte (jamais un lien
            mort ni un faux-vert vers un service arrêté). */}
        {live && dep.url ? (
          <a href={dep.url} target="_blank" rel="noreferrer"
            className="ml-auto text-sm font-medium text-accent-400 hover:underline">
            ouvrir {dep.branch} ↗
          </a>
        ) : (
          <span className="ml-auto text-sm text-faint" title="service non démarré">{dep.branch} ↗</span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" busy={deploy.isPending} disabled={busy}
          onClick={() => deploy.mutate()}>{mounted ? 'Redéployer' : 'Déployer'}</Button>
        <Button variant="ghost" size="sm" busy={stop.isPending} disabled={!mounted || busy}
          onClick={() => stop.mutate()}>Arrêter</Button>
        <Button variant="ghost" size="sm" busy={restart.isPending} disabled={!mounted || busy}
          onClick={() => restart.mutate()}>Redémarrer</Button>
        <Button variant="ghost" size="sm" onClick={() => setLogsOpen((o) => !o)} aria-expanded={logsOpen}>
          {logsOpen ? 'Masquer les logs' : 'Logs'}
        </Button>
      </div>

      {/* Échec d'action honnête (branche invalide, échec build/compose) — jamais un demi-succès silencieux. */}
      {actionError && (
        <Alert tone="danger" title="Action refusée">
          {actionError instanceof ApiError ? actionError.detail : String(actionError)}
        </Alert>
      )}

      {logsOpen && <LogViewer project={project} branch={dep.branch} mounted={mounted} />}
    </Card>
  )
}

/** Log viewer borné : tail à la demande (100/500), `<pre>` mono scrollable. Vide honnête (jamais monté /
 *  rien journalisé). Query pilotée par l'ouverture du panneau (jamais auto). */
function LogViewer({ project, branch, mounted }: { project: string; branch: string; mounted: boolean }) {
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
