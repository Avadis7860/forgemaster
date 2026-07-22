import { useState } from 'react'
import { Alert, Button, LoadingState, RefreshButton } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/cn'
import { useProjectCost } from '@/lib/queries'
import type { CostAcc, CostFeature, ProjectCost } from '@/lib/schemas'

// Formats alignés sur la CLI (`cockpit cost`) : $ à 2 décimales dès $0.01 (repère de coût), 4 en dessous ;
// tokens compacts `1.24M`/`480k`. Le $ vient de Claude — jamais recalculé.
function fmtUsd(v: number): string {
  return v >= 0.01 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`
}
function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}
// $ + tokens compacts pour une rangée (idiome discret de DispatchPanel : séparateur ` · `).
function rowMoney(acc: CostAcc): string {
  return `${fmtUsd(acc.cost_usd)} · ${fmtTokens(acc.tokens)}`
}

/** Bande COÛT sur l'Accueil (idiome RuntimeStrip) : total projet + barre de répartition des tokens
 *  (input/output/cache) + drill-down feature→step à la demande. 100 % tissu (relief par rangée/hover, jamais
 *  carte-dans-carte). Le $ affiché est celui de Claude (`total_cost_usd`), les tokens la vérité. Refresh
 *  manuel (réconcilie après un drain), pas de poll. */
export function CostStrip({ project }: { project: string }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useProjectCost(project)
  return (
    <div className="rounded-card border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-faint">Coût · {project}</span>
        <RefreshButton className="ml-auto" onClick={() => void refetch()} busy={isFetching} />
      </div>
      {isLoading ? (
        <div className="px-3 py-3"><LoadingState label="Calcul du coût…" /></div>
      ) : isError || !data ? (
        <div className="px-3 py-3">
          <Alert tone="danger" title="Coût indisponible">
            {error instanceof ApiError ? error.detail : String(error)}
          </Alert>
        </div>
      ) : data.total.n_jobs === 0 ? (
        <p className="px-3 py-3 text-sm text-muted">
          Aucun coût encore — lance un drain (onglet Travail) : chaque worker enregistre son usage token.
        </p>
      ) : (
        <CostBody data={data} />
      )}
    </div>
  )
}

function CostBody({ data }: { data: ProjectCost }) {
  const { total } = data
  const model = total.model ? ` · ${total.model}` : ''
  const nModels = total.n_models > 1 ? ` (${total.n_models} modèles)` : ''
  return (
    <div>
      {/* Héro : total $ (en relief) + tokens + modèle dominant, puis barre de répartition des tokens. */}
      <div className="space-y-2 px-3 py-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-lg font-semibold tabular-nums text-fg">{fmtUsd(total.cost_usd)}</span>
          <span className="text-sm text-muted">
            {fmtTokens(total.tokens)} tokens
            {/* Désambiguïse le header : le $ est drain-seul, les tokens incluent l'interview (non pricée). Sans
                ce qualificatif, la ligne-relief laisserait lire un ratio $/token de deux périmètres mélangés. */}
            {data.interview && (
              <span className="text-faint"> · dont {fmtTokens(data.interview.tokens)} interactifs (hors $)</span>
            )}
            <span className="text-faint" title={total.model ?? undefined}>{model}{nModels}</span>
          </span>
        </div>
        <ProportionBar total={total} />
      </div>
      {/* Rangées feature (tissu, drill-down par step) + overhead review/outillage + interview (tokens-only). */}
      <div className="divide-y divide-border border-t border-border">
        {data.features.map((f) => <FeatureRow key={f.slug} feature={f} />)}
        {data.nonwork.n_jobs > 0 && (
          <div className="flex items-center justify-between px-3 py-2 text-sm">
            <span className="text-muted">review · outillage</span>
            <span className="tabular-nums text-muted">{rowMoney(data.nonwork)}</span>
          </div>
        )}
        {data.interview && (
          <div className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
            {/* Modèle NON répété ici (le header le porte déjà) → pas de redondance, et pas de wrap à 390px. */}
            <span className="text-muted">interview · cadrage</span>
            {/* $ = « — » (tooltip) : l'interactif n'est pas pricé par Claude ; seuls les tokens comptent (doctrine
                « tokens = vérité, $ = repère »). `whitespace-nowrap` garde « — · 2.57M » insécable à 390px. */}
            <span className="whitespace-nowrap tabular-nums text-muted">
              <span title="non pricé — session interactive, seuls les tokens comptent">—</span>
              {' · '}{fmtTokens(data.interview.tokens)}
            </span>
          </div>
        )}
      </div>
      {data.interview && (
        <p className="border-t border-border px-3 py-2 text-xs text-faint">
          Le $ total est celui du drain — l'interview (interactive) n'est pas pricée par Claude, seuls ses
          tokens comptent dans le total.
        </p>
      )}
    </div>
  )
}

/** Barre de répartition des tokens : cache (read+creation) / entrée / sortie. Largeurs proportionnelles
 *  (géométrie en `style`, tolérée), couleurs par classes littérales de tokens (jamais inline — R4/R5). La
 *  répartition est par TOKENS (on a les comptes exacts ; le $ par type demanderait une table de prix, écartée). */
function ProportionBar({ total }: { total: CostAcc }) {
  const cache = total.cache_read + total.cache_creation
  const sum = total.input + total.output + cache
  if (sum <= 0) return null
  const pct = (n: number) => `${((n / sum) * 100).toFixed(0)}%`
  const seg = (n: number) => ({ width: `${(n / sum) * 100}%` })
  return (
    <div className="space-y-1">
      <div
        className="flex h-2 w-full overflow-hidden rounded-full bg-faint/10"
        role="img"
        aria-label={`répartition des tokens — cache ${pct(cache)}, entrée ${pct(total.input)}, sortie ${pct(total.output)}`}
      >
        <div className="bg-faint/40" style={seg(cache)} />
        <div className="bg-info-500" style={seg(total.input)} />
        <div className="bg-accent-500" style={seg(total.output)} />
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-faint">
        <Legend cls="bg-faint/40" label={`cache ${pct(cache)}`} />
        <Legend cls="bg-info-500" label={`entrée ${pct(total.input)}`} />
        <Legend cls="bg-accent-500" label={`sortie ${pct(total.output)}`} />
      </div>
    </div>
  )
}

function Legend({ cls, label }: { cls: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span aria-hidden className={cn('inline-block h-2 w-2 rounded-full', cls)} />
      {label}
    </span>
  )
}

/** Une feature en rangée tissu : nom + coût à droite, caret ▸ qui déplie les steps (task) + le fix de feature
 *  (relief par hover/caret, pas de carte). Déclencheur = primitive Button (R1). Désactivé si rien à déplier. */
function FeatureRow({ feature }: { feature: CostFeature }) {
  const [open, setOpen] = useState(false)
  const hasDetail = feature.steps.length > 0 || feature.fix != null
  return (
    <div className="px-3">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        disabled={!hasDetail}
        className="h-auto w-full rounded-none px-0 py-2 text-sm font-normal"
      >
        {/* Enfant pleine largeur en justify-between : la valeur $·tokens tombe flush-right, alignée sur les
            steps et la ligne d'overhead (colonne $ scannable) — indépendant du justify-center de Button. */}
        <span className="flex w-full items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-fg">
            <span
              aria-hidden
              className={cn('text-faint transition-transform', open && 'rotate-90', !hasDetail && 'opacity-0')}
            >▸</span>
            {feature.slug}
          </span>
          <span className="tabular-nums text-muted">{rowMoney(feature)}</span>
        </span>
      </Button>
      {open && hasDetail && (
        <div className="space-y-1 pb-2 pl-5 text-xs text-faint">
          {feature.steps.map((s) => (
            <div key={s.task_slug} className="flex items-center justify-between">
              <span>{s.task_slug}</span>
              <span className="tabular-nums">{rowMoney(s)}</span>
            </div>
          ))}
          {feature.fix && (
            <div className="flex items-center justify-between">
              <span>fix (feature)</span>
              <span className="tabular-nums">{rowMoney(feature.fix)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
