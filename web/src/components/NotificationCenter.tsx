import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { Alert, Badge, Button, EmptyState, LoadingState } from '@/components/ui'
import { fraicheur } from '@/lib/instanceFreshness'
import type { Tone } from '@/lib/statusTone'
import { useAckAlert, useAlerts, useInstanceFreshness } from '@/lib/queries'
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
  review_findings: 'findings review',
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

// LE RAPPEL D'INSTANCE — la DEUXIÈME source que ce centre lit (arbitrage du 2026-08-02, option B).
//
// Pourquoi il n'entre PAS dans la table `alerts` : une alerte y est un objet de TRAVAIL — dédupliquée par
// `(project, feature_ref, kind)`, titrée par sa feature, deep-linkée vers elle, et fermée par le succès de
// cette feature. « Ton instance est en retard » n'a ni projet ni feature ; l'y loger obligerait à inventer
// un projet sentinelle et à casser trois contrats pour en réutiliser un. Le centre agrège deux sources,
// `alerts` reste le registre du drain — zéro migration, zéro bump.
//
// Ce n'est pas un ÉVÉNEMENT mais un ÉTAT dérivé : il n'a pas d'`id`, rien à acquitter côté serveur, et il
// s'éteint tout seul dès que l'instance n'est plus en retard.

const CLE_IGNORE = 'forgemaster:instance-stale-dismissed'

/** Le SHA de référence rangé au dernier `✕`. Une **préférence de client**, jamais un état de produit — la
 *  doctrine « rien de l'état d'un geste ne vit ici » vise ce que le SERVEUR possède et relit du disque ;
 *  ceci ne survit qu'au navigateur de celui qui a cliqué, et ne change rien à ce que l'instance rapporte.
 *  Clé = la référence : un nouveau commit dessus ramène le rappel de lui-même, sans rien à ré-armer. */
function lireIgnore(): string | null {
  try {
    return window.localStorage.getItem(CLE_IGNORE)
  } catch {
    return null                              // stockage refusé (mode privé strict) → on rappelle, c'est tout
  }
}

/** La ligne d'instance. Volontairement bâtie sur les MÊMES primitives que `AlertItem` (même `Alert`, même
 *  ✕, même deep-link) : c'est le même geste de lecture pour l'utilisateur, quelle que soit la source. */
function InstanceItem({ etat, onIgnorer, onGo }: {
  etat: ReturnType<typeof fraicheur>
  onIgnorer: () => void
  onGo: () => void
}) {
  return (
    <Alert tone="info" className="text-left">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium text-fg">{etat.titre}</p>
          {etat.detail && <p className="mt-0.5 break-all text-xs text-muted">{etat.detail}</p>}
        </div>
        <button
          type="button"
          onClick={onIgnorer}
          title="Ranger — revient au prochain commit de la référence"
          className="shrink-0 rounded-card px-1.5 py-0.5 text-xs text-muted hover:bg-border/50 hover:text-fg"
        >
          ✕ Ignorer
        </button>
      </div>
      {/* Le deep-link mène là où le geste se fait : le panneau `Mise à jour` vit en tête de `/settings`. */}
      <Link
        to="/settings"
        onClick={onGo}
        className="mt-1.5 inline-block text-xs font-medium text-accent hover:underline"
      >
        Voir les réglages →
      </Link>
    </Alert>
  )
}

export function NotificationCenter() {
  const [open, setOpen] = useState(false)
  const { data, isLoading, isError, refetch } = useAlerts()
  const version = useInstanceFreshness()
  const [ignore, setIgnore] = useState<string | null>(lireIgnore)

  const etat = version.data ? fraicheur(version.data) : null
  // Trois conditions, et la troisième est celle qui rend le badge crédible : on ne pousse QUE sur un retard
  // avéré (`pousse`), jamais sur un « je ne peux pas savoir ». Un centre qui s'allume pour dire qu'il ne
  // sait pas est un centre qu'on apprend à ignorer.
  const instance = etat?.pousse && etat.head !== ignore ? etat : null

  function ignorer() {
    if (!instance?.head) return
    try {
      window.localStorage.setItem(CLE_IGNORE, instance.head)
    } catch {
      // Rangement impossible : la ligne se range quand même pour cette vie de page. Refuser le geste parce
      // que le navigateur refuse la persistance punirait l'utilisateur d'une contrainte qui n'est pas la
      // sienne.
    }
    setIgnore(instance.head)
  }
  // Tri par sévérité (blocker en tête) — le motif qui rend le badge rouge domine la liste (axe 1).
  const alerts = [...(data?.alerts ?? [])].sort(
    (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity],
  )
  const count = (data?.count ?? 0) + (instance ? 1 : 0)
  // Le compteur agrège les deux sources ; la TEINTE, elle, suit la plus grave — et un fait d'instance ne
  // peut jamais rendre le badge rouge, le rouge restant réservé à ce qui BLOQUE le drain. Le troisième cas
  // a été trouvé EN REGARDANT le rendu : sans lui, une instance en retard toute seule allumait un badge
  // `warn` au-dessus d'une ligne `info` — la pastille criait plus fort que ce qu'elle annonçait.
  const tone: Tone = alerts.some((a) => a.severity === 'blocker') ? 'danger'
    : alerts.length > 0 ? 'warn'
      : 'info'

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
            {/* La ligne d'instance vit AU-DESSUS des trois états de la liste d'alertes, et pas dedans : les
                deux sources ont chacune leur chargement et leur panne. Un drain injoignable ne doit pas
                effacer ce que l'instance sait dire d'elle-même. */}
            {instance && (
              <InstanceItem etat={instance} onIgnorer={ignorer} onGo={() => setOpen(false)} />
            )}

            {isLoading ? (
              <LoadingState label="Chargement des alertes…" />
            ) : isError ? (
              <Alert tone="danger" title="Alertes injoignables">
                <div className="flex flex-wrap items-center gap-2">
                  <span>Le daemon n’a pas répondu.</span>
                  <Button variant="ghost" size="sm" onClick={() => refetch()}>
                    Réessayer
                  </Button>
                </div>
              </Alert>
            ) : alerts.length === 0 ? (
              // « Aucune alerte » ne s'écrit QUE si le centre est réellement vide : le poser sous une ligne
              // d'instance ferait se contredire le déroulant à l'écran, badge allumé compris.
              instance ? null
                : <EmptyState title="Aucune alerte" description="Rien ne bloque le drain." />
            ) : (
              <div className={`flex flex-col gap-2${instance ? ' mt-2' : ''}`}>
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
