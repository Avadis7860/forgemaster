import { describe, expect, it } from 'vitest'
import { type FlowEdgeInput, type FlowNodeInput, layoutFlow } from './flowLayout'

// Fabriques minimales : un nœud résolu, une arête résolue, une arête indirecte (callee non résolu).
function node(id: string): FlowNodeInput {
  return { id, label: id.split('::').slice(-1)[0], file: id.split('::')[0] }
}
function edge(from: string, to: string, order = 0, resolution = 'import'): FlowEdgeInput {
  return { from, to, callee_name: to.split('::').slice(-1)[0], resolution, kind: 'call', via: null, order, branch: [] }
}
function indirect(from: string, via: string, order = 0): FlowEdgeInput {
  return { from, to: null, callee_name: 'x', resolution: 'indirect', kind: 'indirect', via, order, branch: [] }
}

const layerOf = (l: ReturnType<typeof layoutFlow>, id: string) =>
  l.columns.findIndex((c) => c.x === l.nodes.find((n) => n.id === id)!.x)

describe('layoutFlow', () => {
  it('empile une chaîne entry→helper→compute en 3 couches (ordre d’exécution)', () => {
    const l = layoutFlow('a::entry', [node('a::entry'), node('a::helper'), node('a::compute')], [
      edge('a::entry', 'a::helper', 1), edge('a::helper', 'a::compute', 1),
    ])
    expect(l.columns).toHaveLength(3)
    expect(layerOf(l, 'a::entry')).toBe(0)
    expect(layerOf(l, 'a::helper')).toBe(1)
    expect(layerOf(l, 'a::compute')).toBe(2)
    expect(l.edges.every((e) => !e.backEdge)).toBe(true)
  })

  it('résout un diamant : la cible commune va au plus long chemin', () => {
    const l = layoutFlow('r', [node('r'), node('b'), node('c'), node('d')], [
      edge('r', 'b', 1), edge('r', 'c', 2), edge('b', 'd', 1), edge('c', 'd', 1),
    ])
    expect(layerOf(l, 'd')).toBe(2)
  })

  it('casse la RÉCURSION : une arête de retour vers un ancêtre est marquée backEdge (pas un plantage)', () => {
    // entry→rec→rec (self-recursion) et rec→entry (retour) : les deux retours sont des back-edges.
    const l = layoutFlow('a::f', [node('a::f'), node('a::g')], [
      edge('a::f', 'a::g', 1), edge('a::g', 'a::f', 1), edge('a::g', 'a::g', 2),
    ])
    const back = l.edges.filter((e) => e.backEdge)
    expect(back.length).toBe(2)                              // g→f (retour) et g→g (self) cassés
    expect(l.edges.find((e) => e.from === 'a::f' && e.to === 'a::g')!.backEdge).toBe(false)
    // le layout reste fini et cohérent (pas de boucle infinie)
    expect(l.width).toBeGreaterThan(0)
    expect(layerOf(l, 'a::g')).toBe(1)
  })

  it('matérialise une arête INDIRECT en feuille suspecte étiquetée par via (canal d’honnêteté)', () => {
    const l = layoutFlow('a::h', [node('a::h')], [indirect('a::h', 'dispatch-table:HANDLERS', 1)])
    const suspect = l.nodes.find((n) => n.indirect)!
    expect(suspect).toBeTruthy()
    expect(suspect.via).toBe('dispatch-table:HANDLERS')
    expect(suspect.label).toBe('dispatch-table:HANDLERS')
    expect(layerOf(l, suspect.id)).toBe(1)                   // en aval du caller
    expect(l.edges).toHaveLength(1)
  })

  it('synthétise une cible hors nodes[] (bornage de profondeur) comme nœud-frontière', () => {
    const l = layoutFlow('a::x', [node('a::x')], [edge('a::x', 'b::deep', 1)])
    const deep = l.nodes.find((n) => n.id === 'b::deep')!
    expect(deep).toBeTruthy()
    expect(deep.indirect).toBe(false)
    expect(deep.label).toBe('deep')
    expect(deep.file).toBe('b')
  })

  it('rend un layout vide sans planter', () => {
    const l = layoutFlow('none', [], [])
    expect(l.nodes).toHaveLength(0)
    expect(l.width).toBe(0)
    expect(l.height).toBe(0)
  })
})
