import { useState } from 'react'
import { Alert, Badge, Button, LoadingState, RefreshButton } from '@/components/ui'
import { LogViewer } from '@/components/runtime/LogViewer'
import { ApiError } from '@/lib/api'
import {
  useDeploy, useDeployments, useReconcileDeployments, useRestartDeployment, useStopDeployment,
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

/** Bandeau Runtime COMPACT (intégré dans la surface Ops, au-dessus du Terminal-ancre) : les 2 déploiements
 *  (`main`/`dev`) en **2 colonnes** côte à côte — chacun une ligne d'actions (branche + statut + Déployer/
 *  Arrêter/Détails) surmontant une méta discrète (port/sha/lien health-gated). Le détail (Redémarrer, erreurs,
 *  logs) est replié à la demande (divulgation progressive). Le refresh réconcilie l'état LIVE (`compose ps`),
 *  jamais en poll. Version réduite de l'ex-onglet Runtime. */
export function RuntimeStrip({ project }: { project: string }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useDeployments(project)
  const reconcile = useReconcileDeployments(project)
  const onRefresh = () => { void refetch(); reconcile.mutate() }

  return (
    <div className="rounded-card border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-faint">Déploiements</span>
        <RefreshButton className="ml-auto" onClick={onRefresh} busy={isFetching || reconcile.isPending} />
      </div>
      {isLoading ? (
        <div className="px-3 py-3"><LoadingState label="Lecture des déploiements…" /></div>
      ) : isError || !data ? (
        <div className="px-3 py-3">
          <Alert tone="danger" title="Runtime indisponible">
            {error instanceof ApiError ? error.detail : String(error)}
          </Alert>
        </div>
      ) : (
        // 2 colonnes côte à côte dès `sm` (les 2 déploiements sur une même bande) ; empilées à 390.
        <div className="grid grid-cols-1 divide-y divide-border sm:grid-cols-2 sm:divide-x sm:divide-y-0">
          {data.deployments.map((dep) => (
            <DeploymentRow key={dep.branch} project={project} dep={dep} />
          ))}
        </div>
      )}
    </div>
  )
}

/** Une cellule compacte de déploiement : ligne d'actions (branche + statut + Déployer/Arrêter/Détails) +
 *  méta discrète (port/sha/lien gaté). Détail (Redémarrer + erreurs + logs) à la demande. Les hooks de
 *  mutation vivent au niveau de la cellule (une cellule = instance stable). */
function DeploymentRow({ project, dep }: { project: string; dep: Deployment }) {
  const deploy = useDeploy(project, dep.branch)
  const stop = useStopDeployment(project, dep.branch)
  const restart = useRestartDeployment(project, dep.branch)
  const [open, setOpen] = useState(false)

  const busy = deploy.isPending || stop.isPending || restart.isPending
  const live = dep.status === 'running'
  const mounted = dep.status !== 'no_deploy'
  const actionError = deploy.error ?? stop.error ?? restart.error

  return (
    <div className="space-y-1.5 px-3 py-2">
      {/* Ligne 1 : branche + statut, actions poussées à droite (Déployer/Arrêter/Détails). */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={gitBranchTone(dep.branch)}>{dep.branch}</Badge>
        <Badge tone={deploymentTone(dep.status)} dot>{STATUS_LABEL[dep.status] ?? dep.status}</Badge>
        <span className="ml-auto flex flex-wrap items-center justify-end gap-1">
          <Button variant="primary" size="sm" busy={deploy.isPending} disabled={busy}
            onClick={() => deploy.mutate()}>{mounted ? 'Redéployer' : 'Déployer'}</Button>
          <Button variant="ghost" size="sm" busy={stop.isPending} disabled={!mounted || busy}
            onClick={() => stop.mutate()}>Arrêter</Button>
          <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}
            title={open ? 'Masquer le détail' : 'Détails (redémarrer, logs)'}>
            <span aria-hidden className="text-faint">{open ? '▾' : '▸'}</span>Détails
          </Button>
        </span>
      </div>

      {/* Ligne 2 : méta discrète — port · sha · lien health-gated (jamais un faux-vert vers un service arrêté). */}
      <div className="flex flex-wrap items-center gap-2 text-xs text-faint">
        {dep.port != null && <code className="font-mono">:{dep.port}</code>}
        {dep.last_deploy_sha && (
          <code className="font-mono" title="sha du dernier deploy">{dep.last_deploy_sha.slice(0, 8)}</code>
        )}
        {live && dep.url ? (
          <a href={dep.url} target="_blank" rel="noreferrer"
            className="font-medium text-accent-400 hover:underline">ouvrir ↗</a>
        ) : (
          <span title="service non démarré">non démarré</span>
        )}
      </div>

      {open && (
        <div className="space-y-2 pt-1">
          <Button variant="ghost" size="sm" busy={restart.isPending} disabled={!mounted || busy}
            onClick={() => restart.mutate()}>Redémarrer</Button>
          {actionError && (
            <Alert tone="danger" title="Action refusée">
              {actionError instanceof ApiError ? actionError.detail : String(actionError)}
            </Alert>
          )}
          <LogViewer project={project} branch={dep.branch} mounted={mounted} />
        </div>
      )}
    </div>
  )
}
