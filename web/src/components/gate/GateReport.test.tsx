import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DecisionBanner, ReviewEvidence, VerifyEvidence } from './GateReport'
import type { MergeDecision, ReviewStatus, VerifyStatus } from '@/lib/schemas'

const GREEN_HOLD: MergeDecision = {
  allow: false,
  decision: 'hold',
  gate_green: true,
  human_go: false,
  ui_touched: false,
  t15_overridden: false,
  t1_overridden: false,
  refixable: false,
  blockers: [],
  reasons: ['Tier-1 : revue fraîche, aucun finding (PASS)'],
}

const RED_BLOCKED: MergeDecision = {
  allow: false,
  decision: 'hold',
  gate_green: false,
  human_go: true,
  ui_touched: true,
  t15_overridden: false,
  t1_overridden: false,
  refixable: true,
  blockers: ['Tier-1 : 1 🔴 reviewer (le rapport bloque le merge) → corriger, ou override humain explicite'],
  reasons: ['Tier-1 : 1 🔴 reviewer (le rapport bloque le merge) → corriger, ou override humain explicite'],
}

describe('DecisionBanner', () => {
  it('gate vert sans GO ⇒ HOLD en attente du GO humain (invariant fail-closed)', () => {
    render(<DecisionBanner decision={GREEN_HOLD} headSha="abcdef1234" />)
    expect(screen.getByText('Gate vert — en attente du GO humain')).not.toBeNull()
    expect(screen.getByText('hold')).not.toBeNull()
    expect(screen.getByText('HEAD abcdef12')).not.toBeNull()
  })

  it('gate rouge ⇒ bloqué, avec les bloqueurs listés', () => {
    render(<DecisionBanner decision={RED_BLOCKED} />)
    expect(screen.getByText('Gate rouge — merge bloqué')).not.toBeNull()
    expect(screen.getByText(/1 🔴 reviewer/)).not.toBeNull()
  })
})

describe('ReviewEvidence', () => {
  it('rend les counts 🔴/🟡/🟣 et le marqueur bloquant quand un verdict frais existe', () => {
    const review: ReviewStatus = {
      present: true,
      fresh: true,
      blocking: true,
      counts: { red: 1, yellow: 2, purple: 0 },
      reviewed_sha: 'deadbeef99',
    }
    render(<ReviewEvidence review={review} />)
    expect(screen.getByText('🔴 1')).not.toBeNull()
    expect(screen.getByText('🟡 2')).not.toBeNull()
    expect(screen.getByText('fraîche')).not.toBeNull()
    expect(screen.getByText('bloquant')).not.toBeNull()
    expect(screen.getByText('revu sur deadbeef')).not.toBeNull()
  })

  it('annonce l’absence de revue quand aucun verdict', () => {
    const review: ReviewStatus = { present: false, fresh: false, blocking: false }
    render(<ReviewEvidence review={review} />)
    expect(screen.getByText('absente')).not.toBeNull()
    expect(screen.getByText(/Aucune revue sur le HEAD/)).not.toBeNull()
  })
})

describe('VerifyEvidence', () => {
  it('N/A quand aucune surface UI touchée', () => {
    const verify: VerifyStatus = { present: false, fresh: false, blocking: false }
    render(<VerifyEvidence verify={verify} uiTouched={false} />)
    expect(screen.getByText('N/A')).not.toBeNull()
    expect(screen.getByText(/preuve e2e non requise/)).not.toBeNull()
  })

  it('rend cibles rendues/échouées quand UI touchée et preuve présente', () => {
    const verify: VerifyStatus = {
      present: true,
      fresh: true,
      ok: false,
      blocking: true,
      n_targets: 3,
      n_failed: 1,
      reviewed_sha: 'cafebabe00',
    }
    render(<VerifyEvidence verify={verify} uiTouched={true} />)
    expect(screen.getByText('1/3 non rendue(s)')).not.toBeNull()
  })
})
