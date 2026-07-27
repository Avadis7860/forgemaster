import { Alert, Badge, Button, EmptyState, LoadingState, RefreshButton } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/cn'
import { useMarkOutcome, useProjectReliability } from '@/lib/queries'
import type { Tone } from '@/lib/statusTone'
import type { Reliability, ReliabilityFeature } from '@/lib/schemas'

// Issue aval d'un merge vert → ton + libellé (source unique statusTone, jamais de couleur inline).
const OUTCOME: Record<ReliabilityFeature['outcome'], { tone: Tone; label: string }> = {
  held: { tone: 'ok', label: 'tenu' },
  reverted: { tone: 'danger', label: 'reverted' },
  refixed: { tone: 'warn', label: 'refixed' },
}

// Taux de fiabilité en % ; `null` (aucun merge vert) = « — ». Ton advisory (repère, pas un seuil dur).
function fmtTaux(taux: number | null): string {
  return taux === null ? '—' : `${Math.round(taux * 100)}%`
}
function tauxTone(taux: number | null): Tone {
  if (taux === null) return 'neutral'
  if (taux >= 0.8) return 'ok'
  return taux >= 0.5 ? 'warn' : 'danger'
}

/** Bande FIABILITÉ sur l'Accueil (idiome CostStrip) : taux du gate vert (merges verts tenus vs revert/refix
 *  marqués aval) + barre tenu/adverse + une rangée par merge vert avec son outcome et l'affordance de MARQUE.
 *  L'attribution est humaine (aucun signal auto) : un merge `held` porte deux boutons (reverted/refixed) ;
 *  `suggested` (rework détecté après le merge) met la rangée en relief — un nudge, jamais compté dans le taux.
 *  100 % tissu (relief par rangée/hover). Refresh manuel (réconcilie après un drain), pas de poll. */
export function ReliabilityStrip({ project }: { project: string }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useProjectReliability(project)
  return (
    <div className="rounded-card border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-faint">
          Fiabilité du gate vert · {project}
        </span>
        <RefreshButton className="ml-auto" onClick={() => void refetch()} busy={isFetching} />
      </div>
      {isLoading ? (
        <div className="px-3 py-3"><LoadingState label="Calcul de la fiabilité…" /></div>
      ) : isError || !data ? (
        <div className="px-3 py-3">
          <Alert tone="danger" title="Fiabilité indisponible">
            {error instanceof ApiError ? error.detail : String(error)}
          </Alert>
        </div>
      ) : (
        <>
          {/* Tempère AVANT le taux : une feature 🔴-bloquée est hors `merge_outcomes` → invisible au taux.
              Rendu même à 0 merge vert (sinon l'EmptyState lirait « tout va bien » sur un projet qui bloque). */}
          {data.n_blocked_open > 0 && <BlockedBanner data={data} />}
          {data.n_merges_verts === 0 ? (
            <div className="px-3 py-3">
              <EmptyState title="Aucun merge vert encore"
                description="Mène un drain jusqu'au merge : chaque merge vert devient un point de mesure." />
            </div>
          ) : (
            <ReliabilityBody project={project} data={data} />
          )}
        </>
      )}
    </div>
  )
}

/** Bandeau tempérant : features 🔴-bloquées (hors `merge_outcomes`, donc hors taux) — remontées pour qu'un
 *  `100%` ne se lise jamais « santé verte » sur un projet qui bloque. Ton `warn`, tissu (rangées). */
function BlockedBanner({ data }: { data: Reliability }) {
  const blocked = data.blocked_features ?? []
  return (
    <div className="px-3 py-3">
      <Alert tone="warn" title={`${data.n_blocked_open} feature(s) 🔴-bloquée(s) — hors taux`}>
        <p className="text-sm">
          Une feature bloquée n'est jamais mergée : elle n'entre pas dans le taux ci-dessous (qui ne couvre
          que les merges verts). Le signal n'est donc pas « tout vert » tant qu'elles bloquent.
        </p>
        {blocked.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-sm">
            {blocked.map((b) => (
              <li key={b.feature_ref} className="flex min-w-0 gap-2">
                <span className="shrink-0 font-medium text-fg">{b.feature}</span>
                <span className="min-w-0 truncate text-faint">{b.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </Alert>
    </div>
  )
}

function ReliabilityBody({ project, data }: { project: string; data: Reliability }) {
  const features = data.features ?? []
  return (
    <div>
      {/* Héro : taux QUALIFIÉ (neutre + badge « provisoire » si aucune marque → borne optimiste, pas preuve)
          + compte verts/adverse/non-jugés, puis barre tenu/adverse. */}
      <div className="space-y-2 px-3 py-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className={cn('text-lg font-semibold tabular-nums',
            toneText(data.provisional ? 'neutral' : tauxTone(data.taux)))}>
            {fmtTaux(data.taux)}
          </span>
          {data.provisional && (
            <Badge tone="warn"
              title="aucune issue aval marquée — le taux est une borne optimiste, pas une fiabilité prouvée">
              provisoire
            </Badge>
          )}
          <span className="text-sm text-muted">
            fiabilité — {data.n_merges_verts} merge{data.n_merges_verts > 1 ? 's' : ''} vert
            {data.n_merges_verts > 1 ? 's' : ''}
            <span className="text-faint"> · {data.n_adverse} adverse
              {data.n_adverse > 0 && ` (${data.n_reverted} revert · ${data.n_refixed} refix)`}
              {' '}· {data.n_held} non jugé{data.n_held > 1 ? 's' : ''}</span>
          </span>
        </div>
        <HeldBar held={data.n_held} adverse={data.n_adverse} />
      </div>
      {/* Rangées : une par merge vert (tissu) — outcome + affordance de marque sur les `held`. */}
      <div className="divide-y divide-border border-t border-border">
        {features.map((f) => <OutcomeRow key={`${f.feature}-${f.sha}`} project={project} feature={f} />)}
      </div>
    </div>
  )
}

/** Barre tenu (ok) / adverse (danger) — proportion des merges verts. Géométrie en `style` (tolérée). */
function HeldBar({ held, adverse }: { held: number; adverse: number }) {
  const sum = held + adverse
  if (sum <= 0) return null
  const pct = (n: number) => `${((n / sum) * 100).toFixed(0)}%`
  return (
    <div
      className="flex h-2 w-full overflow-hidden rounded-full bg-faint/10"
      role="img"
      aria-label={`merges verts — ${pct(held)} tenus, ${pct(adverse)} adverse`}
    >
      <div className="bg-ok-500" style={{ width: pct(held) }} />
      <div className="bg-danger-500" style={{ width: pct(adverse) }} />
    </div>
  )
}

/** Un merge vert en rangée tissu : feature + SHA + badge outcome. Sur un merge `held`, deux boutons de marque
 *  (reverted / refixed) ; `suggested` met la rangée en relief (rework détecté après le merge — un nudge). */
function OutcomeRow({ project, feature: f }: { project: string; feature: ReliabilityFeature }) {
  const mark = useMarkOutcome(project)
  const o = OUTCOME[f.outcome]
  const nudged = f.outcome === 'held' && f.suggested !== null
  return (
    <div className={cn('flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm',
      nudged && 'bg-warn-500/5')}>
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate text-fg">{f.feature}</span>
        <span className="shrink-0 font-mono text-xs text-faint">{f.sha.slice(0, 10)}</span>
        {nudged && (
          <span className="shrink-0 text-xs text-warn-500" title="un run de la feature a suivi le merge">
            rework détecté ?
          </span>
        )}
      </span>
      {f.outcome === 'held' ? (
        // Scent d'action AU REPOS (axe 6) : ton accent + underline-hover → les deux marques se lisent comme des
        // actions, distinctes de la note advisory statique (faint) de la rangée voisine. Égalité légitime (axe 2 :
        // deux issues mutuellement exclusives, aucune n'est « la » primaire).
        <span className="flex shrink-0 items-center gap-2">
          <Button variant="ghost" size="sm" disabled={mark.isPending}
            className="h-auto px-1.5 py-0.5 text-xs text-accent-500 hover:underline"
            onClick={() => mark.mutate({ feature: f.feature, outcome: 'reverted', sha: f.sha })}>
            marquer reverted
          </Button>
          <Button variant="ghost" size="sm" disabled={mark.isPending}
            className="h-auto px-1.5 py-0.5 text-xs text-accent-500 hover:underline"
            onClick={() => mark.mutate({ feature: f.feature, outcome: 'refixed', sha: f.sha })}>
            marquer refixed
          </Button>
        </span>
      ) : (
        <span className="flex shrink-0 items-center gap-1.5">
          {f.note && <span className="max-w-[12rem] truncate text-xs text-faint" title={f.note}>{f.note}</span>}
          <Badge tone={o.tone}>{o.label}</Badge>
          <Button variant="ghost" size="sm" disabled={mark.isPending}
            title="annuler la marque (revenir à « tenu »)"
            className="h-auto px-1 py-0.5 text-xs text-faint"
            onClick={() => mark.mutate({ feature: f.feature, outcome: 'held', sha: f.sha })}>
            ✕
          </Button>
        </span>
      )}
    </div>
  )
}

// Ton → classe de texte (relief du taux héro). Échelle `-500` (charte statusTone) ; neutral = `text-fg`.
// Littéraux fermés (jamais de couleur calculée inline — R4/R5).
function toneText(tone: Tone): string {
  return { ok: 'text-ok-500', warn: 'text-warn-500', danger: 'text-danger-500', neutral: 'text-fg',
    info: 'text-info-500', accent: 'text-accent-500', purple: 'text-purple-500' }[tone]
}
