import { Badge, EmptyState } from '@/components/ui'
import { cn } from '@/lib/cn'
import { edgePath, layoutFeature, NODE_H, NODE_W } from '@/lib/dag'
import { dotClasses, TASK_STATE_TONE, toneFor } from '@/lib/statusTone'
import { stateLabel } from '@/lib/taskLabels'
import type { TaskClassified } from '@/lib/schemas'

function GraphNode({ task, x, y, isNext }: { task: TaskClassified; x: number; y: number; isNext: boolean }) {
  const tone = toneFor(TASK_STATE_TONE, task.state)
  const tip = task.blockers.length ? `${task.title} — bloqué par ${task.blockers.join(', ')}` : (task.title ?? task.slug)
  return (
    <div
      className={cn(
        'absolute flex flex-col justify-center rounded-card border bg-surface px-3 shadow-raised',
        isNext ? 'border-accent-500 ring-2 ring-accent-500/40' : 'border-border',
      )}
      style={{ left: x, top: y, width: NODE_W, height: NODE_H }}
      title={tip}
    >
      <div className="flex items-center gap-1.5">
        <span className={cn('size-2 shrink-0 rounded-pill', dotClasses(tone))} />
        <span className="truncate text-sm font-medium text-fg">{task.title ?? task.slug}</span>
        {isNext && <Badge tone="accent" className="ml-auto shrink-0">NEXT</Badge>}
      </div>
      <div className="mt-0.5 flex items-center gap-2 pl-3.5">
        <code className="truncate text-xs text-faint">{task.slug}</code>
        <span className="ml-auto shrink-0 text-xs text-muted">{stateLabel(task.state)}</span>
      </div>
    </div>
  )
}

/** Graphe node-link d'UNE feature : couches topologiques (via layoutFeature) + arêtes depends_on. */
export function TaskGraph({ tasks, next }: { tasks: TaskClassified[]; next: string | null }) {
  if (tasks.length === 0) {
    return <EmptyState title="Aucune task" description="Ajoute des tasks à cette feature (CLI ou API)." />
  }
  const layout = layoutFeature(tasks)
  return (
    <div className="overflow-x-auto">
      <div className="relative" style={{ width: layout.width, height: layout.height }}>
        {/* En-têtes de colonnes (couche 0…N + cycle) */}
        {layout.columns.map((col) => {
          const x = layout.pos[col.slugs[0]]?.x ?? 0
          return (
            <span
              key={col.label}
              className={cn(
                'absolute text-xs font-semibold uppercase tracking-wider',
                col.cycle ? 'text-danger-500' : 'text-faint',
              )}
              style={{ left: x, top: 0, width: NODE_W }}
            >
              {col.label}
            </span>
          )
        })}

        {/* Arêtes en fond (SVG) — trait plein pour le DAG, pointillé danger pour les cycles */}
        <svg className="pointer-events-none absolute inset-0" width={layout.width} height={layout.height}>
          <defs>
            <marker id="tg-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 Z" fill="var(--color-border-strong)" />
            </marker>
            <marker id="tg-arrow-cycle" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 Z" fill="var(--color-danger-500)" />
            </marker>
          </defs>
          {layout.edges.map((e) => (
            <path
              key={`${e.from}->${e.to}`}
              d={edgePath(layout, e.from, e.to)}
              fill="none"
              stroke={e.cycle ? 'var(--color-danger-500)' : 'var(--color-border-strong)'}
              strokeWidth={1.5}
              strokeDasharray={e.cycle ? '4 3' : undefined}
              markerEnd={e.cycle ? 'url(#tg-arrow-cycle)' : 'url(#tg-arrow)'}
            />
          ))}
        </svg>

        {/* Nœuds (au-dessus des arêtes) */}
        {tasks.map((t) => {
          const p = layout.pos[t.slug]
          if (!p) return null
          return <GraphNode key={t.slug} task={t} x={p.x} y={p.y} isNext={t.slug === next} />
        })}
      </div>
    </div>
  )
}
