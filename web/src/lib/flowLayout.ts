// flowLayout — layering PUR (aucun React) d'un sous-graphe de FLOT d'exécution (`codemap flow`). Jumeau de
// dag.ts, adapté au flot d'appels : (1) arêtes ORDONNÉES (`order` = position de l'appel dans le corps) ;
// (2) la RÉCURSION est un VRAI cycle → détection de back-edge CÔTÉ FRONT (dag.ts la délègue au backend via
// `state==='CYCLE'`, inexistant ici) ; (3) les arêtes INDIRECT (callee non résolu statiquement) deviennent
// des feuilles « suspectes » synthétisées, étiquetées par `via` (canal d'honnêteté — un trou signalé, jamais
// un faux-complet). Déterministe, testable en isolation.

export const NODE_W = 208
export const NODE_H = 52
const COL_GAP = 72
const ROW_GAP = 16
const PAD = 8
export const HEAD_H = 22

// Entrées = le contrat JSON de `codemap flow` (nodes[] + edges[]). `to === null` ⇒ arête indirecte.
export interface FlowNodeInput { id: string; label: string; file: string }
export interface FlowEdgeInput {
  from: string
  to: string | null
  callee_name: string
  resolution: string      // direct | import | self | heuristic | indirect
  kind: string            // call | ctor | indirect
  via: string | null      // "<mécanisme>:<suspect>" si indirect, sinon null
  order: number           // index séquentiel de l'appel dans le corps du caller
  branch: string[]
}

export interface FlowLaidNode {
  id: string; label: string; file: string
  x: number; y: number
  indirect: boolean       // nœud-suspect synthétisé (callee non résolu)
  via: string | null
}
export interface FlowLaidEdge {
  id: string
  from: string; to: string
  resolution: string; kind: string; via: string | null; order: number
  backEdge: boolean       // arête de récursion (retour vers un ancêtre) — rendue en cycle
}
export interface FlowLayout {
  entry: string
  nodes: FlowLaidNode[]
  edges: FlowLaidEdge[]
  columns: { label: string; x: number }[]
  width: number
  height: number
}

function push<K, V>(m: Map<K, V[]>, k: K, v: V): void {
  const list = m.get(k)
  if (list) list.push(v)
  else m.set(k, [v])
}

/** Positionne un sous-graphe de flot : couches = plus long chemin depuis l'entrée (ordre d'exécution
 *  gauche→droite), récursion cassée par détection de back-edge, feuilles indirect en aval du caller. */
export function layoutFlow(
  entry: string, nodesIn: FlowNodeInput[], edgesIn: FlowEdgeInput[],
): FlowLayout {
  const nodeMap = new Map<string, FlowLaidNode>()
  const ensure = (id: string, label: string, file: string, indirect: boolean, via: string | null) => {
    if (!nodeMap.has(id)) nodeMap.set(id, { id, label, file, x: 0, y: 0, indirect, via })
  }
  for (const n of nodesIn) ensure(n.id, n.label, n.file, false, null)

  // 1. Arêtes de rendu : chaque arête pointe vers un id de nœud CONCRET (synthèse des cibles manquantes).
  const edges: FlowLaidEdge[] = []
  edgesIn.forEach((e, i) => {
    let to: string
    if (e.to === null) {
      to = `indirect:${e.from}#${e.order}.${i}`      // feuille suspecte unique (canal d'honnêteté)
      ensure(to, e.via ?? e.callee_name, '', true, e.via)
    } else {
      to = e.to
      if (!nodeMap.has(to)) {                          // cible hors nodes[] (bornage profondeur) → frontière
        const [file, label] = [to.split('::')[0], to.split('::').slice(-1)[0]]
        ensure(to, label, file, false, null)
      }
    }
    edges.push({
      id: `${e.from}->${to}#${i}`, from: e.from, to,
      resolution: e.resolution, kind: e.kind, via: e.via, order: e.order, backEdge: false,
    })
  })

  // 2. Back-edges (récursion) : DFS 3-couleurs depuis l'entrée sur les arêtes RÉSOLUES (les indirect sont des
  //    feuilles, jamais un cycle). Une arête vers un nœud GRIS (sur la pile courante) = retour = back-edge.
  const solidOut = new Map<string, FlowLaidEdge[]>()
  for (const e of edges) if (!nodeMap.get(e.to)!.indirect) push(solidOut, e.from, e)
  const gray = new Set<string>()
  const black = new Set<string>()
  const visit = (u: string) => {
    gray.add(u)
    for (const e of solidOut.get(u) ?? []) {
      if (gray.has(e.to)) e.backEdge = true            // cible sur la pile → récursion
      else if (!black.has(e.to)) visit(e.to)
    }
    gray.delete(u)
    black.add(u)
  }
  if (nodeMap.has(entry)) visit(entry)
  for (const id of nodeMap.keys()) if (!black.has(id) && !nodeMap.get(id)!.indirect) visit(id)

  // 3. Couches = plus long chemin depuis l'entrée (Kahn sur le DAG des arêtes forward résolues). Un nœud
  //    n'est traité qu'après tous ses prédécesseurs → sa couche est le max des (couche(pred)+1).
  const forward = edges.filter((e) => !e.backEdge && !nodeMap.get(e.to)!.indirect)
  const indeg = new Map<string, number>()
  const fout = new Map<string, FlowLaidEdge[]>()
  for (const id of nodeMap.keys()) indeg.set(id, 0)
  for (const e of forward) { indeg.set(e.to, (indeg.get(e.to) ?? 0) + 1); push(fout, e.from, e) }

  const layer = new Map<string, number>()
  const queue: string[] = []
  for (const id of nodeMap.keys()) {
    if (!nodeMap.get(id)!.indirect && (indeg.get(id) ?? 0) === 0) { layer.set(id, 0); queue.push(id) }
  }
  while (queue.length) {
    const u = queue.shift()!
    for (const e of fout.get(u) ?? []) {
      layer.set(e.to, Math.max(layer.get(e.to) ?? 0, (layer.get(u) ?? 0) + 1))
      indeg.set(e.to, (indeg.get(e.to) ?? 0) - 1)
      if ((indeg.get(e.to) ?? 0) === 0) queue.push(e.to)
    }
  }
  // feuilles indirect : une couche à droite de leur caller. Tout nœud non atteint → couche 0 (sûreté).
  for (const e of edges) if (nodeMap.get(e.to)!.indirect) layer.set(e.to, (layer.get(e.from) ?? 0) + 1)
  for (const id of nodeMap.keys()) if (!layer.has(id)) layer.set(id, 0)

  // 4. Colonnes + positions. Ordre intra-colonne = `order` du 1ᵉʳ appel entrant (flot lisible) puis id.
  const maxLayer = Math.max(0, ...[...layer.values()])
  const byLayer: string[][] = Array.from({ length: maxLayer + 1 }, () => [])
  for (const id of nodeMap.keys()) byLayer[layer.get(id) ?? 0].push(id)
  const seq = new Map<string, number>()
  for (const e of edges) if (!seq.has(e.to)) seq.set(e.to, e.order)
  for (const col of byLayer) col.sort((a, b) => (seq.get(a) ?? -1) - (seq.get(b) ?? -1) || a.localeCompare(b))

  byLayer.forEach((col, ci) =>
    col.forEach((id, ri) => {
      const n = nodeMap.get(id)!
      n.x = PAD + ci * (NODE_W + COL_GAP)
      n.y = HEAD_H + PAD + ri * (NODE_H + ROW_GAP)
    }),
  )
  const columns = nodeMap.size
    ? byLayer.map((_, ci) => ({ label: ci === 0 ? 'entrée' : `étape ${ci}`, x: PAD + ci * (NODE_W + COL_GAP) }))
    : []
  const rows = Math.max(0, ...byLayer.map((c) => c.length))
  const width = nodeMap.size ? PAD + maxLayer * (NODE_W + COL_GAP) + NODE_W + PAD : 0
  const height = rows ? HEAD_H + PAD + (rows - 1) * (NODE_H + ROW_GAP) + NODE_H + PAD : 0

  return { entry, nodes: [...nodeMap.values()], edges, columns, width, height }
}

/** Chemin SVG (Bézier cubique) d'une arête, de bord droit de `a` à bord gauche de `b`. Une back-edge
 *  (b à gauche de a) produit une courbe de retour — visuellement distincte, pas un artefact. */
export function flowEdgePath(a: FlowLaidNode, b: FlowLaidNode): string {
  const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2
  const mx = (x1 + x2) / 2
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`
}
