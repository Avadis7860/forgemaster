import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GateVerdicts } from '@/lib/schemas'

// Holder hoisté : `data` pilote `useGateVerdicts` (le verdict complet servi par `GET …/verdicts`).
const h = vi.hoisted(() => ({ data: undefined as GateVerdicts | undefined }))

vi.mock('@/lib/queries', () => ({
  useGateVerdicts: () => ({ isLoading: false, isError: false, error: null, data: h.data }),
}))

const { ConsultativeFindings } = await import('./ConsultativeFindings')

describe('ConsultativeFindings — surfacing des 🟡/🟣 consultatifs', () => {
  it('count 0 → ne rend rien (pas de bruit quand aucun consultatif)', () => {
    const { container } = render(<ConsultativeFindings project="p" feature="f" count={0} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('count>0 → titre + corps des findings consultatifs, jamais les 🔴', () => {
    h.data = {
      review: {
        findings: [
          { severity: '🟡', category: 'a11y', file: 'contrast.ts', line: 41, claim: 'ratio 3.9:1',
            evidence: 'contrast.ts:41 — <h2>' },
          { severity: '🟣', file: 'tokens.test.ts', line: 12, claim: 'token orphelin' },
          { severity: '🔴', file: 'x.ts', line: 1, claim: 'bloquant' },
        ],
      },
      toolchain: null,
    } as GateVerdicts
    render(<ConsultativeFindings project="p" feature="f" count={2} />)
    expect(screen.getByText(/Findings consultatifs — 2/)).toBeInTheDocument()
    expect(screen.getByText('ratio 3.9:1')).toBeInTheDocument()
    expect(screen.getByText('contrast.ts:41')).toBeInTheDocument()
    expect(screen.getByText('token orphelin')).toBeInTheDocument()
    expect(screen.queryByText('bloquant')).toBeNull() // le 🔴 bloque ailleurs, pas surfacé ici
  })
})
