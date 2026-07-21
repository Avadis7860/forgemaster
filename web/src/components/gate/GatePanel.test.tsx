import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { GateStatus, MergeDecision, RefixResult } from '@/lib/schemas'

// Holder hoisté : pilote `useGate` (décision affichée) et `useRefixDispatch` (l'offre de correction) par test.
const h = vi.hoisted(() => ({
  gateData: undefined as GateStatus | undefined,
  refixData: undefined as RefixResult | undefined,
  refixMutate: vi.fn(),
  refixPending: false,
}))

vi.mock('@/lib/queries', () => ({
  useGate: () => ({
    isLoading: false, isError: false, isFetching: false, error: null,
    data: h.gateData, refetch: vi.fn(),
  }),
  useMerge: () => ({ isPending: false, isError: false, error: null, data: undefined, mutate: vi.fn() }),
  useReviewDispatch: () => ({ isPending: false, isError: false, error: null, data: undefined, mutate: vi.fn() }),
  useRefixDispatch: () => ({
    isPending: h.refixPending, isError: false, error: null,
    data: h.refixData, mutate: h.refixMutate,
  }),
}))

const { GatePanel } = await import('./GatePanel')

function decision(over: Partial<MergeDecision> = {}): MergeDecision {
  return {
    allow: false, decision: 'hold', gate_green: false, human_go: false, ui_touched: false,
    t15_overridden: false, t1_overridden: false, refixable: false, blockers: [], reasons: [],
    ...over,
  }
}

function gate(d: MergeDecision): GateStatus {
  return {
    head_sha: 'abcdef1234', ui_touched: false,
    review: { present: true, fresh: true, blocking: false, counts: { red: 0, yellow: 0, purple: 0 } },
    verify: { present: false, fresh: false, blocking: false },
    decision: d,
  } as GateStatus
}

const feature = { slug: 'build' } as never

beforeEach(() => {
  h.gateData = undefined
  h.refixData = undefined
  h.refixMutate.mockClear()
  h.refixPending = false
})

describe('GatePanel — offre de correction sur gate rouge', () => {
  it('gate rouge REFIXABLE → bouton d\'offre, qui dispatche la passe de correction', () => {
    h.gateData = gate(decision({ gate_green: false, refixable: true, blockers: ['Tier-0 natif : ruff'] }))
    render(<GatePanel project="atlas" feature={feature} />)
    const btn = screen.getByRole('button', { name: /Dispatcher une passe de correction/i })
    fireEvent.click(btn)
    expect(h.refixMutate).toHaveBeenCalledTimes(1)
  })

  it('gate rouge NON refixable (garde de process) → aucune offre de correction', () => {
    h.gateData = gate(decision({ gate_green: false, refixable: false, blockers: ['Tier-1 : aucune revue'] }))
    render(<GatePanel project="atlas" feature={feature} />)
    expect(screen.queryByRole('button', { name: /passe de correction/i })).toBeNull()
  })

  it('gate VERT → aucune offre de correction (rien à corriger)', () => {
    h.gateData = gate(decision({ gate_green: true, refixable: false }))
    render(<GatePanel project="atlas" feature={feature} />)
    expect(screen.queryByRole('button', { name: /passe de correction/i })).toBeNull()
  })

  it('après la passe → compte-rendu HUMAIN (statut, passe n/N, prochaine étape, bloqueurs)', () => {
    h.gateData = gate(decision({ gate_green: false, refixable: true, blockers: ['Tier-0 natif : ruff'] }))
    h.refixData = {
      status: 'still_red', feature: 'build', gate_green: false, fix_pass: 1, max_passes: 3,
      blockers: ['Tier-0 natif : mypy'], head_sha: 'beef99', next_step: 'toujours rouge — relance ou reprends la main.',
    }
    render(<GatePanel project="atlas" feature={feature} />)
    const alert = screen.getByText('Toujours rouge').closest('[role="alert"]')?.textContent ?? ''
    expect(alert).toContain('Passe 1/3')                        // borne située
    expect(alert).toContain('toujours rouge')                   // prochaine étape en clair
    expect(alert).toContain('mypy')                             // bloqueur ré-évalué cité
  })
})
