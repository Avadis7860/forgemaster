import { useState } from 'react'
import { useParams, useSearch } from '@tanstack/react-router'
import { Alert, Button, Card, EmptyState, Input, LoadingState, RefreshButton } from '@/components/ui'
import { DecisionBanner, ReviewEvidence, VerifyEvidence } from '@/components/gate/GateReport'
import { ApiError } from '@/lib/api'
import { useGate, useMerge, useRoadmap } from '@/lib/queries'
import type { FeatureWithTasks } from '@/lib/schemas'

/** Onglet Gate : lit la vue Gate d'une feature (statut brut Tier-1/Tier-1.5 + décision composée en preview
 *  GO=false) et expose le **GO humain** de merge. Invariant fail-closed : gate vert SANS go ⇒ hold, jamais
 *  merge (le backend décide ; le front ne recompose jamais la décision). */
export function GateTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { feature: deepFeature } = useSearch({ strict: false }) as { feature?: string }
  const roadmap = useRoadmap(project)

  const features = roadmap.data?.features ?? []
  const [selected, setSelected] = useState<string | null>(null)
  // Feature effective : sélection explicite → deep-link → 1ʳᵉ non mergée → 1ʳᵉ feature.
  const active =
    selected ?? deepFeature ?? features.find((f) => f.status !== 'merged')?.slug ?? features[0]?.slug ?? null
  const feature = features.find((f) => f.slug === active) ?? null

  if (roadmap.isLoading)
    return <div className="p-8"><LoadingState label="Chargement des features…" /></div>
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
          title="Aucune feature à passer au gate"
          description="Ajoute une feature et dispatche ses tasks (CLI cockpit ou API) : le gate évalue la branche produite."
        />
      </div>
    )
  }

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-wrap gap-1.5">
          {/* Sélecteur de feature = toggle de sélection, PAS l'action primaire (axe 2) : l'accent plein est
              réservé au GO. La sélection se marque par une bordure accent (pas de remplissage teal concurrent). */}
          {features.map((f) => (
            <Button
              key={f.id}
              size="sm"
              variant={f.slug === active ? 'secondary' : 'ghost'}
              className={f.slug === active ? 'border-accent-500 text-fg' : undefined}
              onClick={() => setSelected(f.slug)}
            >
              {f.title ?? f.slug}
              {f.status === 'merged' && <span className="ml-1.5 opacity-70">· mergée</span>}
            </Button>
          ))}
        </div>
        <RefreshButton onClick={() => roadmap.refetch()} busy={roadmap.isFetching} />
      </div>
      {feature && <GatePanel key={feature.id} project={project} feature={feature} />}
    </div>
  )
}

/** Panneau d'une feature : décision de merge (bannière), évidence Tier-1/Tier-1.5, overrides + GO humain. */
function GatePanel({ project, feature }: { project: string; feature: FeatureWithTasks }) {
  const gate = useGate(project, feature.slug)
  const merge = useMerge(project, feature.slug)
  const [t1Override, setT1Override] = useState('')
  const [t15Override, setT15Override] = useState('')

  if (gate.isLoading)
    return <Card className="p-5"><LoadingState label="Évaluation du gate…" /></Card>
  if (gate.isError || !gate.data) {
    return (
      <Card className="space-y-3 p-5">
        <Alert tone="danger" title="Gate indisponible">
          {gate.error instanceof ApiError ? gate.error.detail : String(gate.error)}
        </Alert>
        <RefreshButton onClick={() => gate.refetch()} busy={gate.isFetching} />
      </Card>
    )
  }

  const data = gate.data
  const decision = data.decision ?? null

  // Une feature jamais dispatchée n'a pas de branche → rien à merger (le backend renvoie decision=null).
  if (!decision) {
    return (
      <Card className="p-5">
        <EmptyState
          title="Aucune branche à merger"
          description="Cette feature n'a pas encore été dispatchée : aucune branche de travail, donc rien à évaluer ni à merger."
        />
      </Card>
    )
  }

  // Overridable côté humain : un 🔴 reviewer (Tier-1) ou un Tier-1.5 absent/périmé/non-rendu quand l'UI est
  // touchée. JAMAIS un Tier-0 / natif déterministe. On ne montre les champs que si un tel bloqueur existe.
  const t1Overridable = data.review.blocking
  const t15Overridable =
    data.ui_touched && (!data.verify.present || !data.verify.fresh || data.verify.blocking)
  const showOverrides = t1Overridable || t15Overridable

  // GO actif si gate vert (hold → merge) OU si un override explicite est saisi (tentative de levée d'un
  // bloqueur overridable). Le backend tranche : jamais de recomposition de la décision côté front.
  const overridePresent = t1Override.trim() !== '' || t15Override.trim() !== ''
  const canGo = decision.gate_green || overridePresent

  function onGo() {
    merge.mutate({
      go: true,
      t1_override: t1Override.trim() || undefined,
      t15_override: t15Override.trim() || undefined,
    })
  }

  const report = merge.data

  return (
    <Card className="space-y-5 p-5">
      <DecisionBanner decision={decision} headSha={data.head_sha} />

      <div className="grid gap-4 sm:grid-cols-2">
        <ReviewEvidence review={data.review} />
        <VerifyEvidence verify={data.verify} uiTouched={data.ui_touched} />
      </div>

      {decision.reasons.length > 0 && (
        <details className="rounded-card border border-border px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-muted">
            Chaîne d'autorité — {decision.reasons.length} point(s)
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-muted">
            {decision.reasons.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-faint">·</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {showOverrides && (
        <div className="space-y-3 rounded-card border border-warn-500/30 bg-warn-500/5 px-4 py-3">
          <p className="text-sm font-medium text-fg">Override humain (raison explicite, tracée)</p>
          {t1Overridable && (
            <label className="block space-y-1">
              <span className="text-xs text-muted">Lever le 🔴 reviewer (Tier-1)</span>
              <Input
                value={t1Override}
                onChange={(e) => setT1Override(e.target.value)}
                placeholder="Pourquoi ce 🔴 est levable à la main…"
              />
            </label>
          )}
          {t15Overridable && (
            <label className="block space-y-1">
              <span className="text-xs text-muted">Lever le Tier-1.5 (preuve e2e)</span>
              <Input
                value={t15Override}
                onChange={(e) => setT15Override(e.target.value)}
                placeholder="Pourquoi le rendu est acceptable sans preuve fraîche…"
              />
            </label>
          )}
          <p className="text-xs text-faint">
            Un override ne lève qu'un 🔴 reviewer ou un Tier-1.5 — jamais un veto Tier-0 / toolchain native.
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <p className="text-sm text-muted">
          {decision.gate_green
            ? 'Le gate est vert. Le merge attend ton GO — le LLM ne merge jamais seul.'
            : 'Le gate est rouge. Corrige les bloqueurs, ou fournis un override explicite pour ceux qui sont levables.'}
        </p>
        <Button variant="primary" onClick={onGo} busy={merge.isPending} disabled={!canGo || merge.isPending}>
          {merge.isPending ? 'Merge en cours…' : 'GO — merger la feature'}
        </Button>
      </div>

      {merge.isError && (
        <Alert tone="danger" title="Échec du merge">
          {merge.error instanceof ApiError ? merge.error.detail : String(merge.error)}
        </Alert>
      )}
      {report?.merged && (
        <Alert tone="ok" title="Feature mergée">
          <p>
            ff dev→main promu{report.merge_sha ? ` (sha ${report.merge_sha.slice(0, 8)})` : ''} ·{' '}
            {report.closed_tasks.length} task(s) fermée(s)
            {report.pending_tasks.length > 0 && ` · ${report.pending_tasks.length} task(s) todo restante(s)`}.
          </p>
        </Alert>
      )}
      {report && !report.merged && (
        <Alert tone="warn" title="Merge non effectué">
          {report.reason}
        </Alert>
      )}
    </Card>
  )
}
