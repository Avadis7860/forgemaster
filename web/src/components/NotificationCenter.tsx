import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui'
import type { Tone } from '@/lib/statusTone'
import { useAckAlert, useAlerts } from '@/lib/queries'
import type { Alert as AlertRow } from '@/lib/schemas'

/** Centre d'alertes du header (v17, no-silent-block) : un badge-compteur toujours monté + un centre déroulant
 *  listant les blocages de drain ouverts, chacun avec son motif et un deep-link « Aller à la feature » (click
 *  and go) vers sa surface Travail. Poll court (`useAlerts`) → un blocage remonte POUSSÉ, sans naviguer vers la
 *  page de la feature. Relief par TOKEN (tons de la source unique statusTone), pas par un panneau décoratif
 *  (axe 1 « tissu > panneau ») ; états vide/chargement/erreur traités (axe 7). */

// Étiquettes COURTES (le motif détaillé vit dans `reason`) — évite la troncature mi-mot sur la ligne titre.
const KIND_LABEL: Record<AlertRow['kind'], string> = {
  gate_red: 'gate rouge',
  worker_failed: 'échec worker',
  rate_limited: 'rate-limit',
  interrupted: 'interrompu',
  socle_hold: 'attente socle',
  interview_hold: 'interview',
}

const SEVERITY_TONE: Record<AlertRow['severity'], Tone> = {
  blocker: 'danger',
  warn: 'warn',
  info: 'info',
}

// Rang de tri : le blocker (celui qui rend le badge rouge) remonte EN TÊTE — relief par position (axe 1).
const SEVERITY_RANK: Record<AlertRow['severity'], number> = { blocker: 0, warn: 1, info: 2 }

/** Rend `findings` (payload JSON libre : liste de blockers, ou objet) en une ligne compacte, ou null. */
function findingsLine(findings: unknown): string | null {
  if (Array.isArray(findings)) {
    const parts = findings.filter((f): f is string => typeof f === 'string')
    return parts.length ? parts.join(' · ') : null
  }
  return null
}

function AlertItem({ alert, onGo }: { alert: AlertRow; onGo: () => void }) {
  const ack = useAckAlert()
  // Findings n'est montré que s'il AJOUTE de l'info : sinon il duplique le motif (axe 5, anti-redondance).
  const rawDetail = findingsLine(alert.findings)
  const detail =
    rawDetail && !rawDetail.startsWith(alert.reason) && !alert.reason.startsWith(rawDetail)
      ? rawDetail
      : null
  return (
    <Alert tone={SEVERITY_TONE[alert.severity]} className="text-left">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-fg">
            {alert.feature}
            <span className="ml-1.5 text-xs font-normal text-muted">{KIND_LABEL[alert.kind]}</span>
            {alert.tier && <span className="ml-1 text-xs font-normal text-faint">{alert.tier}</span>}
          </p>
          <p className="mt-0.5 text-xs text-muted">{alert.reason}</p>
          {detail && <p className="mt-0.5 truncate text-xs text-faint" title={detail}>{detail}</p>}
        </div>
        <button
          type="button"
          onClick={() => ack.mutate(alert.id)}
          disabled={ack.isPending}
          title="Acquitter — retire l’alerte du compteur"
          className="shrink-0 rounded-card px-1.5 py-0.5 text-xs text-muted hover:bg-border/50 hover:text-fg disabled:opacity-50"
        >
          ✕ Ignorer
        </button>
      </div>
      <Link
        to="/$project/travail"
        params={{ project: alert.project }}
        search={{ feature: alert.feature }}
        onClick={onGo}
        className="mt-1.5 inline-block text-xs font-medium text-accent hover:underline"
      >
        Aller à la feature →
      </Link>
    </Alert>
  )
}

export function NotificationCenter() {
  const [open, setOpen] = useState(false)
  const { data, isLoading, isError, refetch } = useAlerts()
  // Tri par sévérité (blocker en tête) — le motif qui rend le badge rouge domine la liste (axe 1).
  const alerts = [...(data?.alerts ?? [])].sort(
    (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity],
  )
  const count = data?.count ?? 0
  const tone: Tone = alerts.some((a) => a.severity === 'blocker') ? 'danger' : 'warn'

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`Alertes (${count})`}
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-card px-1.5 py-1 text-muted hover:text-fg"
      >
        <span aria-hidden>🔔</span>
        {count > 0 && (
          <Badge tone={tone} dot>
            {count}
          </Badge>
        )}
      </button>

      {open && (
        <>
          {/* Scrim invisible (patron du tiroir mobile) : ferme au clic hors du centre, sans listener document. */}
          <button
            type="button"
            aria-hidden
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-(--z-dock) cursor-default"
          />
          <div
            role="dialog"
            aria-label="Centre d'alertes"
            className="absolute right-0 z-(--z-drawer) mt-2 max-h-[70vh] w-80 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-card border border-border bg-surface p-3 shadow-lg"
          >
            {isLoading ? (
              <LoadingState label="Chargement des alertes…" />
            ) : isError ? (
              <Alert tone="danger" title="Alertes injoignables">
                Le daemon n’a pas répondu.{' '}
                <button type="button" onClick={() => refetch()} className="font-medium underline">
                  Réessayer
                </button>
              </Alert>
            ) : alerts.length === 0 ? (
              <EmptyState title="Aucune alerte" description="Rien ne bloque le drain." />
            ) : (
              <div className="flex flex-col gap-2">
                {alerts.map((a) => (
                  <AlertItem key={a.id} alert={a} onGo={() => setOpen(false)} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
