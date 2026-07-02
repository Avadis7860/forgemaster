// dag — layering PUR (aucun React) du DAG d'UNE feature pour le rendu node-link. Le backend a déjà
// classé les tasks (resolver.classify : state DONE/ACTIVE/READY/BLOCKED_DEPS/CYCLE/ERROR) ; ici on ne
// fait que de la GÉOMÉTRIE : couche = plus long chemin de dépendances (intra-feature), les nœuds CYCLE
// partent dans une colonne dédiée à droite, les arêtes relient dep → task. Testable en isolation.
import type { TaskClassified } from './schemas'

// Dimensions des nœuds/colonnes (SoT unique — le composant les réutilise pour dimensionner à l'identique).
export const NODE_W = 196
export const NODE_H = 60
const COL_GAP = 64
const ROW_GAP = 18
const PAD = 8
export const HEAD_H = 26

export interface DagColumn { label: string; slugs: string[]; cycle: boolean }
export interface DagEdge { from: string; to: string; cycle: boolean }
export interface DagLayout {
  columns: DagColumn[]
  pos: Record<string, { x: number; y: number }>
  edges: DagEdge[]
  width: number
  height: number
}

/** Positionne les tasks classées d'une feature en couches topologiques + colonne cycle. Déterministe. */
export function layoutFeature(tasks: TaskClassified[]): DagLayout {
  const bySlug = new Map(tasks.map((t) => [t.slug, t]))
  const isCycle = (s: string) => bySlug.get(s)?.state === 'CYCLE'
  const layer = new Map<string, number>()

  // Plus long chemin sur le sous-graphe ACYCLIQUE (deps cycle/absentes ignorées). `seen` = garde de
  // sécurité (le sous-graphe hors-cycle est un DAG, mais on ne fait jamais confiance aveuglément).
  const compute = (slug: string, seen: Set<string>): number => {
    const cached = layer.get(slug)
    if (cached !== undefined) return cached
    const t = bySlug.get(slug)
    const deps = (t?.depends_on ?? []).filter((d) => bySlug.has(d) && !isCycle(d) && !seen.has(d))
    const l = deps.length ? 1 + Math.max(...deps.map((d) => compute(d, new Set(seen).add(slug)))) : 0
    layer.set(slug, l)
    return l
  }

  const acyclic = tasks.filter((t) => t.state !== 'CYCLE')
  const cyclic = tasks.filter((t) => t.state === 'CYCLE')
  acyclic.forEach((t) => compute(t.slug, new Set()))
  const nLayers = acyclic.length ? Math.max(...acyclic.map((t) => layer.get(t.slug)!)) + 1 : 0

  const columns: DagColumn[] = []
  for (let l = 0; l < nLayers; l++) {
    columns.push({
      label: `couche ${l}`,
      slugs: acyclic.filter((t) => layer.get(t.slug) === l).map((t) => t.slug),
      cycle: false,
    })
  }
  if (cyclic.length) columns.push({ label: 'cycle', slugs: cyclic.map((t) => t.slug), cycle: true })

  const pos: Record<string, { x: number; y: number }> = {}
  columns.forEach((col, ci) =>
    col.slugs.forEach((s, ri) => {
      pos[s] = { x: PAD + ci * (NODE_W + COL_GAP), y: HEAD_H + PAD + ri * (NODE_H + ROW_GAP) }
    }),
  )

  const edges: DagEdge[] = []
  for (const t of tasks) {
    for (const d of t.depends_on) {
      if (pos[d] && pos[t.slug]) edges.push({ from: d, to: t.slug, cycle: t.state === 'CYCLE' || isCycle(d) })
    }
  }

  const maxRows = columns.length ? Math.max(...columns.map((c) => c.slugs.length)) : 0
  const width = columns.length ? PAD + (columns.length - 1) * (NODE_W + COL_GAP) + NODE_W + PAD : 0
  const height = maxRows ? HEAD_H + PAD + (maxRows - 1) * (NODE_H + ROW_GAP) + NODE_H + PAD : 0

  return { columns, pos, edges, width, height }
}

/** Chemin SVG (courbe de Bézier) d'une arête dep → task, de bord droit à bord gauche. */
export function edgePath(layout: DagLayout, from: string, to: string): string {
  const a = layout.pos[from]
  const b = layout.pos[to]
  if (!a || !b) return ''
  const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2
  const mx = (x1 + x2) / 2
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`
}
