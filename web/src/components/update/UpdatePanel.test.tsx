import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import type { UpdateRun } from '@/lib/schemas'

const MORT = new ApiError(0, 'daemon injoignable (TypeError: Failed to fetch)')

const h = vi.hoisted(() => ({
  runs: null as unknown,
  runsError: null as unknown,
  run: null as unknown,
  runError: null as unknown,
  contact: 0,
}))

vi.mock('@/lib/queries', () => ({
  useUpdateRuns: () => ({
    data: h.runs, error: h.runsError, isFetching: false, dataUpdatedAt: h.contact, refetch: vi.fn(),
  }),
  useUpdateRun: () => ({
    data: h.run, error: h.runError, isFetching: false, dataUpdatedAt: h.contact, refetch: vi.fn(),
  }),
  useVersion: () => ({
    data: { version: '0.1.0', sha: 'abcdef1234', committed_at: '2026-08-07T00:00:00Z',
            comparable: false, stale: null, behind_by: null, missing_types: [] },
    error: null, isFetching: false, dataUpdatedAt: h.contact, refetch: vi.fn(),
  }),
  useApplyUpdate: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, error: null }),
  useRollbackUpdate: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, error: null }),
  // Consommés par les enfants — neutralisés ici : ce fichier teste l'orchestration, pas l'aire de dépôt.
  useWheels: () => ({ data: { wheels: [], total: 0, keep: 3, max_bytes: 67108864 }, isPending: false,
                      isError: false, error: null }),
  useStageWheel: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false,
                          data: undefined, error: null }),
  useUpdatePlan: () => ({ data: null, error: null, isError: false, isPending: true }),
}))

const { UpdatePanel } = await import('./UpdatePanel')

function run(over: Partial<UpdateRun> = {}): UpdateRun {
  return {
    run: '2026-08-07T10-00-00Z', mode: 'apply', scope: 'user', unit: 'u', started_at: null,
    target: null, state: 'running', rc: null, verdict: '', impact: null, journal: '', ...over,
  }
}

describe('UpdatePanel — ce qui est montré quand le backend meurt en plein geste', () => {
  it("appelle ATTENDU le silence qui suit un geste, et n'affiche AUCUN échec", () => {
    // Le cœur du sujet. Le daemon qu'on vient de charger de se remplacer ne répond plus : c'est l'issue
    // nominale. Une UI naïve écrirait « erreur » ici — au moment précis où tout se passe bien.
    h.runs = { runs: [run()], total: 1, truncated: false, follow_timeout: 900 }
    h.runsError = MORT
    h.run = run()
    h.runError = MORT
    h.contact = Date.now() - 5_000

    render(<UpdatePanel />)

    expect(screen.getByRole('status')).toHaveTextContent(/attendu/)
    expect(screen.queryByText(/Le geste n'est pas parti/)).not.toBeInTheDocument()
  })

  it('ne maquille pas en bascule un daemon mort qu\'aucun geste n\'explique', () => {
    // Le contre-témoin, et il compte plus que le cas nominal : si ce silence-ci passait pour une bascule,
    // le panneau dirait « c'est normal » chaque fois que l'instance tombe.
    h.runs = { runs: [run({ state: 'done', rc: 0, verdict: 'MAJ posée' })], total: 1, truncated: false,
               follow_timeout: 900 }
    h.runsError = MORT
    h.run = run({ state: 'done', rc: 0, verdict: 'MAJ posée' })
    h.runError = MORT
    h.contact = Date.now() - 5_000

    render(<UpdatePanel />)

    expect(screen.getByRole('status')).toHaveTextContent(/injoignable/)
    expect(screen.getByRole('status')).toHaveTextContent(/n'est pas une bascule/)
  })

  it('ne qualifie aucun silence tant que l\'instance répond', () => {
    h.runs = { runs: [], total: 0, truncated: false, follow_timeout: 900 }
    h.runsError = null
    h.run = null
    h.runError = null
    h.contact = Date.now()

    render(<UpdatePanel />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByText(/Cette instance sert le build/)).toBeInTheDocument()
  })
})
