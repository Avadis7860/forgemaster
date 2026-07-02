import { describe, expect, it } from 'vitest'
import { layoutFeature } from './dag'
import type { TaskClassified } from './schemas'

// Fabrique une task classée minimale (les champs non pertinents au layering sont neutres).
function mk(slug: string, state: string, depends_on: string[] = []): TaskClassified {
  return {
    id: slug, feature_id: 'f', slug, title: slug, status: 'todo', priority: 'P1',
    created_at: '2026-01-01', depends_on, state, blockers: [],
  }
}

// Retourne l'index de colonne où vit un slug.
function colOf(cols: { slugs: string[] }[], slug: string): number {
  return cols.findIndex((c) => c.slugs.includes(slug))
}

describe('layoutFeature', () => {
  it('empile une chaîne a→b→c en 3 couches croissantes', () => {
    const { columns, edges } = layoutFeature([
      mk('a', 'READY'), mk('b', 'BLOCKED_DEPS', ['a']), mk('c', 'BLOCKED_DEPS', ['b']),
    ])
    expect(columns).toHaveLength(3)
    expect(colOf(columns, 'a')).toBe(0)
    expect(colOf(columns, 'b')).toBe(1)
    expect(colOf(columns, 'c')).toBe(2)
    expect(edges).toEqual([
      { from: 'a', to: 'b', cycle: false },
      { from: 'b', to: 'c', cycle: false },
    ])
  })

  it('place un fan-out (root→b, root→c) dans la même couche', () => {
    const { columns } = layoutFeature([
      mk('root', 'READY'), mk('b', 'BLOCKED_DEPS', ['root']), mk('c', 'BLOCKED_DEPS', ['root']),
    ])
    expect(colOf(columns, 'b')).toBe(1)
    expect(colOf(columns, 'c')).toBe(1)
  })

  it('résout un diamant a→{b,c}→d : d en couche 2 (plus long chemin)', () => {
    const { columns } = layoutFeature([
      mk('a', 'READY'), mk('b', 'BLOCKED_DEPS', ['a']), mk('c', 'BLOCKED_DEPS', ['a']),
      mk('d', 'BLOCKED_DEPS', ['b', 'c']),
    ])
    expect(colOf(columns, 'd')).toBe(2)
  })

  it('isole les nœuds CYCLE dans une colonne dédiée à droite + marque leurs arêtes', () => {
    const { columns, edges } = layoutFeature([
      mk('ok', 'READY'), mk('x', 'CYCLE', ['y']), mk('y', 'CYCLE', ['x']),
    ])
    const cyc = columns.at(-1)!
    expect(cyc.cycle).toBe(true)
    expect(cyc.slugs.sort()).toEqual(['x', 'y'])
    expect(edges.every((e) => e.cycle)).toBe(true)
    expect(edges).toHaveLength(2)   // x→y et y→x, tous deux tracés
  })

  it('rend un layout vide sans planter', () => {
    const { columns, width, height } = layoutFeature([])
    expect(columns).toHaveLength(0)
    expect(width).toBe(0)
    expect(height).toBe(0)
  })
})
