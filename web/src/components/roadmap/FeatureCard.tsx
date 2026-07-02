import { Badge, Card } from '@/components/ui'
import { cn } from '@/lib/cn'
import { FEATURE_STATUS_TONE, toneFor } from '@/lib/statusTone'
import type { FeatureWithTasks } from '@/lib/schemas'
import { TaskGraph } from './TaskGraph'

/** Carte d'une feature : en-tête (titre, statut, avancement, NEXT) + son graphe de tasks. */
export function FeatureCard({ feature, highlighted = false }: {
  feature: FeatureWithTasks
  highlighted?: boolean
}) {
  const done = feature.tasks.filter((t) => t.state === 'DONE').length
  return (
    <Card
      as="section"
      id={`feature-${feature.slug}`}
      className={cn('overflow-hidden scroll-mt-4', highlighted && 'ring-2 ring-accent-500/50')}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-surface-raised px-4 py-2.5">
        <span className="text-sm font-semibold text-fg">{feature.title ?? feature.slug}</span>
        <code className="text-xs text-faint">{feature.slug}</code>
        <Badge tone={toneFor(FEATURE_STATUS_TONE, feature.status)}>{feature.status}</Badge>
        <div className="ml-auto flex items-center gap-3">
          {feature.next && (
            <Badge tone="accent" dot>
              NEXT · {feature.next}
            </Badge>
          )}
          <span className="text-xs tabular-nums text-muted">{done}/{feature.tasks.length} fait</span>
        </div>
      </div>
      <div className="p-4">
        <TaskGraph tasks={feature.tasks} next={feature.next} />
      </div>
    </Card>
  )
}
