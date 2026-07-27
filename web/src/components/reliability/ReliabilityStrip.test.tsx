import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Reliability, ReliabilityFeature } from '@/lib/schemas'

// Holder hoisté : `data` pilote `useProjectReliability` ; `markMutate` est le spy de la mutation de marque.
const h = vi.hoisted(() => ({
  data: undefined as Reliability | undefined,
  isError: false,
  markMutate: vi.fn(),
  refetch: vi.fn(),
}))

vi.mock('@/lib/queries', () => ({
  useProjectReliability: () => ({
    data: h.data, isLoading: false, isError: h.isError, error: null, refetch: h.refetch, isFetching: false,
  }),
  useMarkOutcome: () => ({ mutate: h.markMutate, isPending: false }),
}))

const { ReliabilityStrip } = await import('./ReliabilityStrip')

function mkFeat(over: Partial<ReliabilityFeature> = {}): ReliabilityFeature {
  return {
    feature: 'corridor', sha: 'deadbeef0123', human_go: true, outcome: 'held',
    suggested: null, note: null, merged_at: '2026-07-26T00:00:00Z', marked_at: null, ...over,
  }
}

function mkData(over: Partial<Reliability> = {}): Reliability {
  return {
    scope: 'project', project: 'atlas', n_merges_verts: 1, n_reverted: 0, n_refixed: 0,
    n_adverse: 0, n_held: 1, n_marked: 1, provisional: false, n_blocked_open: 0,
    taux: 1, features: [mkFeat()], ...over,
  }
}

beforeEach(() => {
  h.data = undefined
  h.isError = false
  h.markMutate.mockClear()
  h.refetch.mockClear()
})

describe('ReliabilityStrip — fiabilité du gate vert', () => {
  it('rend le taux (%) et le compte de merges verts', () => {
    h.data = mkData({ n_merges_verts: 4, n_adverse: 1, n_reverted: 1, n_held: 3, taux: 0.75 })
    render(<ReliabilityStrip project="atlas" />)
    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(screen.getByText(/4 merges verts/)).toBeInTheDocument()
    expect(screen.getByText(/1 adverse/)).toBeInTheDocument()
  })

  it('aucun merge vert → EmptyState (axe 7), pas de taux', () => {
    h.data = mkData({ n_merges_verts: 0, n_held: 0, taux: null, features: [] })
    render(<ReliabilityStrip project="atlas" />)
    expect(screen.getByText('Aucun merge vert encore')).toBeInTheDocument()
    expect(screen.queryByText('—')).toBeNull()
  })

  it('« marquer reverted » sur un merge held → mutation avec feature+outcome+sha', () => {
    h.data = mkData()
    render(<ReliabilityStrip project="atlas" />)
    fireEvent.click(screen.getByRole('button', { name: /marquer reverted/i }))
    expect(h.markMutate).toHaveBeenCalledWith({ feature: 'corridor', outcome: 'reverted', sha: 'deadbeef0123' })
  })

  it('une pré-suggestion (rework aval) met la rangée held en relief (nudge, axe 1)', () => {
    h.data = mkData({ features: [mkFeat({ suggested: 'refixed' })] })
    render(<ReliabilityStrip project="atlas" />)
    expect(screen.getByText('rework détecté ?')).toBeInTheDocument()
  })

  it('provisoire (aucune marque) → badge « provisoire », le 100% ne se lit pas vert-santé', () => {
    h.data = mkData({ n_merges_verts: 3, n_adverse: 0, n_held: 3, n_marked: 0, provisional: true, taux: 1 })
    render(<ReliabilityStrip project="atlas" />)
    expect(screen.getByText('100%')).toBeInTheDocument()
    expect(screen.getByText('provisoire')).toBeInTheDocument()
    expect(screen.getByText(/3 non jugés/)).toBeInTheDocument()
  })

  it('feature 🔴-bloquée → bandeau « hors taux », même à 0 merge vert', () => {
    h.data = mkData({
      n_merges_verts: 0, n_held: 0, taux: null, features: [], n_blocked_open: 1,
      blocked_features: [{ feature_ref: 'atlas/design-system', feature: 'design-system',
        reason: 'Tier-1.5 : 1 cible non rendue' }],
    })
    render(<ReliabilityStrip project="atlas" />)
    expect(screen.getByText(/1 feature\(s\) 🔴-bloquée\(s\) — hors taux/)).toBeInTheDocument()
    expect(screen.getByText('design-system')).toBeInTheDocument()
    expect(screen.getByText('Aucun merge vert encore')).toBeInTheDocument()   // les deux coexistent
  })

  it('un merge marqué reverted porte son badge, pas de bouton de marque', () => {
    h.data = mkData({
      n_adverse: 1, n_reverted: 1, n_held: 0, taux: 0,
      features: [mkFeat({ outcome: 'reverted', marked_at: '2026-07-27T00:00:00Z', note: 'régression prod' })],
    })
    render(<ReliabilityStrip project="atlas" />)
    expect(screen.getByText('reverted')).toBeInTheDocument()
    expect(screen.getByText('régression prod')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /marquer reverted/i })).toBeNull()
  })
})
