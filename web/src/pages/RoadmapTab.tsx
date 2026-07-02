import { useEffect } from 'react'
import { useParams, useSearch } from '@tanstack/react-router'
import { Alert, EmptyState, LoadingState, RefreshButton } from '@/components/ui'
import { FeatureCard } from '@/components/roadmap/FeatureCard'
import { StateLegend } from '@/components/roadmap/StateLegend'
import { useRoadmap } from '@/lib/queries'

/** Onglet Roadmap (home du workspace projet) : le DAG des features×tasks, une carte-graphe par feature. */
export function RoadmapTab() {
  const project = useParams({ strict: false }).project ?? ''
  const { feature } = useSearch({ strict: false }) as { feature?: string }
  const { data, isLoading, isError, error, refetch, isFetching } = useRoadmap(project)

  // Deep-link `?feature=<slug>` : amène la carte visée dans le viewport (read-only, aucun effet).
  useEffect(() => {
    if (feature && data) document.getElementById(`feature-${feature}`)?.scrollIntoView({ block: 'start' })
  }, [feature, data])

  if (isLoading) return <div className="p-8"><LoadingState label="Chargement de la roadmap…" /></div>
  if (isError) {
    return (
      <div className="space-y-3 p-8">
        <Alert tone="danger" title="Roadmap indisponible">{error.message}</Alert>
        <RefreshButton onClick={() => refetch()} busy={isFetching} />
      </div>
    )
  }
  if (!data || data.features.length === 0) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          title="Roadmap vide"
          description="Aucune feature. Ajoute une feature et ses tasks (CLI cockpit ou API) pour voir le graphe de séquencement."
        />
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between gap-4">
        <StateLegend />
        <RefreshButton onClick={() => refetch()} busy={isFetching} />
      </div>
      <div className="space-y-6">
        {data.features.map((f) => (
          <FeatureCard key={f.id} feature={f} highlighted={f.slug === feature} />
        ))}
      </div>
    </div>
  )
}
