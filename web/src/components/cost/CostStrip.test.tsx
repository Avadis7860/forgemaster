import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// État injectable du hook de coût (hoisté pour la factory vi.mock). On teste le RENDU : total + barre de
// répartition + drill-down feature→step, et l'honnête-vide.
const h = vi.hoisted(() => ({ result: {} as Record<string, unknown> }))

vi.mock('@/lib/queries', () => ({
  useProjectCost: () => h.result,
}))

const { CostStrip } = await import('./CostStrip')

const acc = (cost: number, tokens: number, split?: Partial<Record<string, number>>) => ({
  cost_usd: cost, input: 0, output: 0, cache_read: 0, cache_creation: 0, tokens, n_jobs: 1, ...split,
})

const ok = (data: unknown) => ({
  data, isLoading: false, isError: false, error: null, refetch: vi.fn(), isFetching: false,
})

const POPULATED = {
  project: 'atlas',
  total: {
    cost_usd: 2.34, input: 149_000, output: 211_000, cache_read: 700_000, cache_creation: 180_000,
    tokens: 1_240_000, n_jobs: 5, model: 'claude-opus-4-8', n_models: 2,
  },
  features: [
    { slug: 'auth', ...acc(0.89, 480_000, { n_jobs: 2 }),
      steps: [{ task_slug: 'login', ...acc(0.41, 210_000) }], fix: null },
    { slug: 'billing', ...acc(1.10, 610_000, { n_jobs: 3 }),
      steps: [{ task_slug: 'signup', ...acc(0.48, 270_000) }], fix: acc(0.21, 130_000) },
  ],
  nonwork: acc(0.35, 150_000),
}

describe('CostStrip', () => {
  beforeEach(() => { h.result = ok(POPULATED) })

  it('rend le total $, les tokens, le modèle dominant et la barre de répartition', () => {
    render(<CostStrip project="atlas" />)
    expect(screen.getByText('$2.34')).toBeInTheDocument()
    expect(screen.getByText(/1\.24M tokens/)).toBeInTheDocument()
    expect(screen.getByText(/claude-opus-4-8 \(2 modèles\)/)).toBeInTheDocument()
    // barre = image accessible ; légende présente
    expect(screen.getByRole('img', { name: /répartition des tokens/ })).toBeInTheDocument()
    expect(screen.getByText(/cache 71%/)).toBeInTheDocument()   // 880k/1240k ≈ 71 %
  })

  it('liste les features + la ligne review·outillage, steps repliés par défaut', () => {
    render(<CostStrip project="atlas" />)
    expect(screen.getByText('auth')).toBeInTheDocument()
    expect(screen.getByText('billing')).toBeInTheDocument()
    expect(screen.getByText('review · outillage')).toBeInTheDocument()
    // drill-down replié : les steps ne sont pas rendus tant qu'on n'a pas ouvert
    expect(screen.queryByText('login')).not.toBeInTheDocument()
  })

  it('déplie une feature → ses steps + le fix de feature apparaissent', () => {
    render(<CostStrip project="atlas" />)
    fireEvent.click(screen.getByText('billing'))
    expect(screen.getByText('signup')).toBeInTheDocument()
    expect(screen.getByText('fix (feature)')).toBeInTheDocument()
  })

  it('honnête-vide : aucun job → ligne muted, pas de barre ni d\'erreur', () => {
    h.result = ok({ project: 'atlas', total: { ...acc(0, 0), n_jobs: 0, model: null, n_models: 0 },
                    features: [], nonwork: { ...acc(0, 0), n_jobs: 0 } })
    render(<CostStrip project="atlas" />)
    expect(screen.getByText(/Aucun coût encore/)).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /répartition/ })).not.toBeInTheDocument()
  })

  it('erreur → Alert (jamais un faux-vert)', () => {
    h.result = { data: undefined, isLoading: false, isError: true, error: new Error('boom'),
                 refetch: vi.fn(), isFetching: false }
    render(<CostStrip project="atlas" />)
    expect(screen.getByText('Coût indisponible')).toBeInTheDocument()
  })
})
