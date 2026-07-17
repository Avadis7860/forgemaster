import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { FeatureWithTasks } from '@/lib/schemas'

// Trois runs d'une même feature : un run task échoué (en tête → transcript sélectionné par défaut, avec sa
// raison d'échec), un run REVIEWER (kind='review' ancré à la task 'interview' — le squat que F2 a rendu
// honnête), un run task vert. On prouve que le front rend le GENRE (plus jamais le slug squatté) + l'erreur.
const JOBS = [
  { id: 'fa11ed00', kind: 'task', task_slug: 'parser-core', status: 'failed',
    error: 'claude -p rc=1 : ToolPreflightError — `ruff` introuvable' },
  { id: 'de1e6a7e', kind: 'review', task_slug: 'interview', status: 'done' },
  { id: 'c0ffee11', kind: 'task', task_slug: 'parser-core', status: 'done' },
]

vi.mock('@/lib/queries', () => ({
  useOnboarding: () => ({ data: { claude_auth: { authenticated: true } } }),
  useDispatch: () => ({ isPending: false, isError: false, error: null, data: undefined, mutate: vi.fn() }),
  useFeatureJobs: () => ({ data: JOBS }),
  useJob: () => ({ data: { job: JOBS[0], events: [] }, isLoading: false }),
}))

vi.mock('@/lib/useDispatchStream', () => ({
  useDispatchStream: () => ({ events: [], status: 'idle', terminal: null }),
}))

const { DispatchPanel } = await import('./DispatchPanel')

const FEATURE = { slug: 'ingest-pipeline', next: undefined, tasks: [] } as unknown as FeatureWithTasks

describe('DispatchPanel — visibilité honnête du run reviewer', () => {
  it('un run review affiche son GENRE et n\'usurpe plus le slug de la task qu\'il ancre', () => {
    render(<DispatchPanel project="atlas" feature={FEATURE} />)
    // Le run review est distinctement étiqueté « review »…
    expect(screen.getByText('review')).toBeInTheDocument()
    // …et le slug SQUATTÉ ('interview') n'est PLUS rendu (le badge de genre le remplace) — le grief de bosse.
    expect(screen.queryByText('interview')).toBeNull()
    // Un run task garde son slug (le cas courant, inchangé).
    expect(screen.getAllByText('parser-core').length).toBeGreaterThan(0)
  })

  it('la raison d\'échec persistée (colonne error) est surfacée sous le transcript', () => {
    render(<DispatchPanel project="atlas" feature={FEATURE} />)
    expect(screen.getByText('Raison de l\'échec')).toBeInTheDocument()
    expect(screen.getByText(/ToolPreflightError/)).toBeInTheDocument()
  })
})
