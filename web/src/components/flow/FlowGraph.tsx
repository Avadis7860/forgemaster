import { Badge, EmptyState } from '@/components/ui'
import { cn } from '@/lib/cn'
import {
  type FlowLaidEdge,
  type FlowLaidNode,
  flowEdgePath,
  layoutFlow,
  NODE_H,
  NODE_W,
} from '@/lib/flowLayout'
import type { Flow } from '@/lib/schemas'

// Couleurs d'arête par nature (tokens du design-system) : résolu sûr = neutre, heuristique = info (confiance
// basse), INDIRECT = warn (canal d'honnêteté), RÉCURSION = purple. Jamais une arête inventée passée pour sûre.
const COLOR = {
  strong: 'var(--color-border-strong)',
  info: 'var(--color-info-500)',
  warn: 'var(--color-warn-500)',
  purple: 'var(--color-purple-500)',
} as const

function edgeColor(e: FlowLaidEdge, targetIndirect: boolean): string {
  if (e.backEdge) return COLOR.purple
  if (targetIndirect || e.resolution === 'indirect') return COLOR.warn
  if (e.resolution === 'heuristic') return COLOR.info
  return COLOR.strong
}

function FlowNodeBox({ node, isEntry }: { node: FlowLaidNode; isEntry: boolean }) {
  return (
    <div
      className={cn(
        'absolute flex flex-col justify-center rounded-card border px-3',
        node.indirect
          ? 'border-dashed bg-surface-raised'
          : 'bg-surface shadow-raised',
        isEntry ? 'border-accent-500 ring-2 ring-accent-500/40' : node.indirect ? '' : 'border-border',
      )}
      style={{
        left: node.x, top: node.y, width: NODE_W, height: NODE_H,
        borderColor: node.indirect ? COLOR.warn : undefined,
      }}
      title={node.indirect ? `appel indirect — ${node.via ?? 'mécanisme dynamique'}` : `${node.file} · ${node.label}`}
    >
      {node.indirect ? (
        <>
          <div className="flex items-center gap-1.5">
            <span className="size-2 shrink-0 rounded-pill" style={{ background: COLOR.warn }} />
            <span className="truncate text-sm font-medium" style={{ color: COLOR.warn }}>{node.via ?? node.label}</span>
          </div>
          <span className="mt-0.5 pl-3.5 text-xs text-faint">indirect · non résolu statiquement</span>
        </>
      ) : (
        <>
          <span className="truncate text-sm font-medium text-fg">{node.label}</span>
          <code className="mt-0.5 truncate text-xs text-faint">{node.file}</code>
        </>
      )}
    </div>
  )
}

/** Graphe node-link d'un FLOT d'exécution : couches = ordre d'exécution (gauche→droite), arêtes résolues
 *  pleines, INDIRECT en pointillé warn + label `via`, RÉCURSION en pointillé purple. Bandeau d'honnêteté
 *  (indirect_ratio) en tête — un flot partiel est signalé, jamais présenté comme complet. Scroll (pas de
 *  zoom/pan en P3a). */
export function FlowGraph({ flow }: { flow: Flow }) {
  const nodes = flow.nodes ?? []
  const edges = flow.edges ?? []
  if (!flow.ok) {
    return <EmptyState title="Flot indisponible" description={flow.reason ?? 'Opération introuvable ou ambiguë.'} />
  }
  if (nodes.length === 0) {
    return <EmptyState title="Flot vide" description="Aucun appel interne résolu pour cette opération." />
  }

  const layout = layoutFlow(flow.entry ?? nodes[0].id, nodes, edges)
  const byId = new Map(layout.nodes.map((n) => [n.id, n]))
  const ratio = flow.stats ? Math.round(flow.stats.indirect_ratio * 100) : 0

  return (
    <div className="space-y-3">
      {/* Bandeau d'honnêteté : quelle fraction du flot est réellement résolue (le reste est en pointillé). */}
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <Badge tone={ratio === 0 ? 'accent' : 'warn'} dot>
          {ratio === 0 ? 'flot entièrement résolu' : `${ratio} % des appels non résolus statiquement`}
        </Badge>
        {ratio > 0 && <span>— arêtes <span style={{ color: COLOR.warn }}>en pointillé</span> = indirection signalée (jamais devinée)</span>}
        {flow.stats && <span className="ml-auto tabular-nums">{flow.stats.nodes} nœuds · {flow.stats.edges} appels</span>}
      </div>

      <div className="overflow-x-auto">
        <div className="relative" style={{ width: layout.width, height: layout.height }}>
          {/* En-têtes de colonnes (entrée · étape N) */}
          {layout.columns.map((col) => (
            <span
              key={col.label}
              className="absolute text-xs font-semibold uppercase tracking-wider text-faint"
              style={{ left: col.x, top: 0, width: NODE_W }}
            >
              {col.label}
            </span>
          ))}

          {/* Arêtes en fond (SVG) — ordre d'appel annoté, indirect/récursion en pointillé */}
          <svg className="pointer-events-none absolute inset-0" width={layout.width} height={layout.height}>
            <defs>
              {(['strong', 'info', 'warn', 'purple'] as const).map((k) => (
                <marker key={k} id={`fl-arrow-${k}`} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                  <path d="M0,0 L7,3.5 L0,7 Z" fill={COLOR[k]} />
                </marker>
              ))}
            </defs>
            {layout.edges.map((e) => {
              const a = byId.get(e.from)
              const b = byId.get(e.to)
              if (!a || !b) return null
              const color = edgeColor(e, b.indirect)
              const markerKey = e.backEdge ? 'purple' : b.indirect || e.resolution === 'indirect' ? 'warn'
                : e.resolution === 'heuristic' ? 'info' : 'strong'
              const dashed = e.backEdge || b.indirect || e.resolution === 'indirect'
              const mx = (a.x + NODE_W + b.x) / 2
              const my = (a.y + b.y) / 2 + NODE_H / 2
              return (
                <g key={e.id}>
                  <path
                    d={flowEdgePath(a, b)}
                    fill="none"
                    stroke={color}
                    strokeWidth={1.5}
                    strokeDasharray={dashed ? '5 4' : undefined}
                    markerEnd={`url(#fl-arrow-${markerKey})`}
                  />
                  {/* ordre d'appel (le « puis ») — petit repère au départ de l'arête */}
                  <text x={mx} y={my - 3} textAnchor="middle" fontSize="9" fill="var(--color-faint)">
                    {e.order}
                  </text>
                </g>
              )
            })}
          </svg>

          {/* Nœuds (au-dessus des arêtes) */}
          {layout.nodes.map((n) => (
            <FlowNodeBox key={n.id} node={n} isEntry={n.id === layout.entry} />
          ))}
        </div>
      </div>
    </div>
  )
}
