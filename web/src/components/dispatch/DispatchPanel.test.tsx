import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FeatureRunReport, FeatureWithTasks } from '@/lib/schemas'

// Trois runs d'une même feature : un run task échoué (en tête → transcript sélectionné par défaut, avec sa
// raison d'échec), un run REVIEWER (kind='review' ancré à la task 'interview' — le squat que F2 a rendu
// honnête), un run task vert. On prouve que le front rend le GENRE (plus jamais le slug squatté) + l'erreur.
const JOBS = [
  { id: 'fa11ed00', kind: 'task', task_slug: 'parser-core', status: 'failed',
    error: 'claude -p rc=1 : ToolPreflightError — `ruff` introuvable' },
  { id: 'de1e6a7e', kind: 'review', task_slug: 'interview', status: 'done' },
  { id: 'c0ffee11', kind: 'task', task_slug: 'parser-core', status: 'done' },
]

// Holder hoisté : `dispatchData` pilote le retour de `useDispatch` par test ; `navigate` est le spy du router
// (DispatchPanel navigue vers l'onglet Ops pour le handoff interview).
const h = vi.hoisted(() => ({ dispatchData: undefined as FeatureRunReport | undefined, navigate: vi.fn(),
  dispatchPending: false, abortMutate: vi.fn() }))

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => h.navigate }))

vi.mock('@/lib/queries', () => ({
  useOnboarding: () => ({ data: { claude_auth: { authenticated: true } } }),
  useDispatch: () => ({ isPending: h.dispatchPending, isError: false, error: null, data: h.dispatchData, mutate: vi.fn() }),
  useAbort: () => ({ isPending: false, isError: false, error: null, mutate: h.abortMutate }),
  useFeatureJobs: () => ({ data: JOBS }),
  useJob: () => ({ data: { job: JOBS[0], events: [] }, isLoading: false }),
}))

vi.mock('@/lib/useDispatchStream', () => ({
  useDispatchStream: () => ({ events: [], status: 'idle', terminal: null }),
}))

const { DispatchPanel } = await import('./DispatchPanel')

const FEATURE = { slug: 'ingest-pipeline', next: undefined, tasks: [] } as unknown as FeatureWithTasks

beforeEach(() => {
  h.dispatchData = undefined
  h.dispatchPending = false
  h.navigate.mockClear()
  h.abortMutate.mockClear()
})

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

describe('DispatchPanel — arrêt du run (abort)', () => {
  it('sans run actif, aucun bouton « Arrêter le run »', () => {
    render(<DispatchPanel project="atlas" feature={FEATURE} />)   // job en tête = failed → pas de run actif
    expect(screen.queryByRole('button', { name: 'Arrêter le run' })).toBeNull()
  })

  it('un run actif affiche « Arrêter le run » ; confirmer déclenche l\'abort', () => {
    h.dispatchPending = true                                      // POST dispatch en cours → run actif
    render(<DispatchPanel project="atlas" feature={FEATURE} />)
    fireEvent.click(screen.getByRole('button', { name: 'Arrêter le run' }))   // header (dialog fermé)
    expect(screen.getByText('Arrêter le run ?')).toBeInTheDocument()          // confirmation ouverte
    const buttons = screen.getAllByRole('button', { name: 'Arrêter le run' }) // header + confirm dialog
    fireEvent.click(buttons[buttons.length - 1])                              // confirmer
    expect(h.abortMutate).toHaveBeenCalledTimes(1)                            // l'abort est déclenché
  })
})

describe('DispatchPanel — handoff interview (socle interactif)', () => {
  // Feature de socle dont la NEXT task est `interactive` : l'affordance interview est PROACTIVE (issue de la
  // roadmap, avant tout clic) — pas derrière une mutation dispatch. C'est le fix du « bouton dispatcher muet ».
  const SOCLE = {
    slug: 'socle-design', next: 'cadrage',
    tasks: [{ slug: 'cadrage', title: 'Cadrage', status: 'todo', mode: 'interactive',
              state: 'READY', blockers: [], depends_on: [], priority: 'P0' }],
  } as unknown as FeatureWithTasks

  it('next interactive → l\'action primaire est « Lancer l\'interview », pas « Dispatcher »', () => {
    render(<DispatchPanel project="atlas" feature={SOCLE} />)
    expect(screen.getByRole('button', { name: /Lancer l'interview/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Dispatcher/i })).toBeNull()
    expect(screen.getByText('Interview de 1ʳᵉ session requise')).toBeInTheDocument()
  })

  it('le clic OUVRE l\'onglet Ops en auto-lançant l\'interview (search param run=interview)', () => {
    render(<DispatchPanel project="atlas" feature={SOCLE} />)
    fireEvent.click(screen.getByRole('button', { name: /Lancer l'interview/i }))
    expect(h.navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/$project/ops', params: { project: 'atlas' }, search: { run: 'interview' } }),
    )
  })
})
