import { useState } from 'react'
import { useParams, useSearch } from '@tanstack/react-router'
import { Alert, Button, Collapsible, EmptyState, LoadingState, RefreshButton } from '@/components/ui'
import { DispatchPanel } from '@/components/dispatch/DispatchPanel'
import { GatePanel } from '@/components/gate/GatePanel'
import { ApiError } from '@/lib/api'
import { useGate, useRoadmap } from '@/lib/queries'
import type { FeatureWithTasks } from '@/lib/schemas'

/** Onglet « Travail » : la boucle forge d'une feature en UNE surface staged — dispatch (produire une branche)
 *  puis validation + GO merge (la valider). Fusion des ex-onglets Dispatch et Gate : une seule boucle, un seul
 *  sélecteur de feature, une action primaire à la fois (divulgation progressive — le gate se révèle quand une
 *  branche existe). */
export function TravailTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { feature: deepFeature } = useSearch({ strict: false }) as { feature?: string }
  const roadmap = useRoadmap(project)

  const features = roadmap.data?.features ?? []
  const [selected, setSelected] = useState<string | null>(null)
  // Feature effective : sélection explicite → deep-link → 1ʳᵉ feature actionnable (task prête OU branche à
  // valider) → 1ʳᵉ non mergée → 1ʳᵉ feature.
  const active =
    selected ??
    deepFeature ??
    features.find((f) => f.next || f.worktree_path)?.slug ??
    features.find((f) => f.status !== 'merged')?.slug ??
    features[0]?.slug ??
    null
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
          title="Aucune feature à travailler"
          description="Ajoute une feature et ses tasks (CLI cockpit ou API) : ici tu dispatches sa NEXT task puis valides et merges la branche produite."
        />
      </div>
    )
  }

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-wrap gap-1.5">
          {/* Sélecteur de feature = toggle, PAS l'action primaire (axe 2) : l'accent plein est réservé aux
              boutons « Dispatcher » / « GO ». La sélection se marque par une bordure accent. */}
          {features.map((f) => (
            <Button
              key={f.id}
              size="sm"
              variant={f.slug === active ? 'secondary' : 'ghost'}
              className={f.slug === active ? 'border-accent-500 text-fg' : undefined}
              onClick={() => setSelected(f.slug)}
            >
              {f.title ?? f.slug}
              {f.next ? (
                <span className="ml-1.5 opacity-70">· prêt</span>
              ) : f.status === 'merged' ? (
                <span className="ml-1.5 opacity-70">· mergée</span>
              ) : null}
            </Button>
          ))}
        </div>
        <RefreshButton onClick={() => roadmap.refetch()} busy={roadmap.isFetching} />
      </div>
      {feature && <TravailPanel key={feature.id} project={project} feature={feature} />}
    </div>
  )
}

/** Composition staged d'une feature : stepper « Dispatch → Valider & merger » + les deux panneaux, le stage
 *  inactif replié (divulgation progressive). Le stage se dérive de l'existence d'une branche : `worktree_path`
 *  (signal gratuit, déjà sur le roadmap) confirmé par la décision du gate (`decision != null`). */
function TravailPanel({ project, feature }: { project: string; feature: FeatureWithTasks }) {
  const gate = useGate(project, feature.slug) // partagé (même queryKey) avec le GatePanel — une seule lecture
  const hasBranch = feature.worktree_path != null || gate.data?.decision != null
  const stage: 'dispatch' | 'gate' = hasBranch ? 'gate' : 'dispatch'

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-3">
          <Step n="1" label="Dispatch" state={stage === 'dispatch' ? 'active' : 'done'} />
          <span aria-hidden className="text-faint">→</span>
          <Step n="2" label="Valider & merger" state={stage === 'gate' ? 'active' : 'pending'} />
        </div>
        <p className="mt-1.5 text-xs text-faint">
          La boucle produit une branche, la valide, puis la merge — sans changer d'onglet.
        </p>
      </div>

      {/* Étape 1 — Dispatch : primaire tant qu'aucune branche ; repliée en résumé une fois la branche produite. */}
      {stage === 'gate' ? (
        <Collapsible title={`Dispatch — branche produite · ${feature.branch}`}>
          <DispatchPanel project={project} feature={feature} />
        </Collapsible>
      ) : (
        <DispatchPanel project={project} feature={feature} />
      )}

      {/* Étape 2 — Valider & merger : révélée quand une branche existe ; repliée en attente sinon. */}
      {stage === 'dispatch' ? (
        <Collapsible title="Valider & merger — en attente d'une branche">
          <GatePanel project={project} feature={feature} />
        </Collapsible>
      ) : (
        <GatePanel project={project} feature={feature} />
      )}
    </div>
  )
}

/** Puce d'étape du stepper : cercle numéroté + libellé. `done` = ✓ vert, `active` = accent cuivre, `pending`
 *  = neutre en retrait. Relief par valeur de token (pas de panneau décoratif). */
function Step({ n, label, state }: { n: string; label: string; state: 'done' | 'active' | 'pending' }) {
  const circle =
    state === 'done'
      ? 'bg-ok-500/20 text-ok-500'
      : state === 'active'
        ? 'bg-accent-600 text-on-accent'
        : 'border border-border-strong bg-surface-raised text-muted'
  return (
    <span className="flex items-center gap-2 text-sm">
      <span className={`grid h-5 w-5 place-items-center rounded-full text-xs font-medium ${circle}`}>
        {state === 'done' ? '✓' : n}
      </span>
      <span className={state === 'pending' ? 'text-muted' : 'font-medium text-fg'}>{label}</span>
    </span>
  )
}
