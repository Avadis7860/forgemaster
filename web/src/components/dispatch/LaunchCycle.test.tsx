import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReconcileSocleResult, Roadmap } from '@/lib/schemas'

// Holder hoisté : pilote `useRoadmapCheck` (gate de complétude) et `useReconcileSocle` (l'action) par test.
const h = vi.hoisted(() => ({
  checkOk: true,
  reconcileData: undefined as ReconcileSocleResult | undefined,
  reconcileMutate: vi.fn(),
  reconcilePending: false,
}))

vi.mock('@/lib/queries', () => ({
  useRoadmapCheck: () => ({ data: { ok: h.checkOk } }),
  useReconcileSocle: () => ({
    isPending: h.reconcilePending, isError: false, error: null,
    data: h.reconcileData, mutate: h.reconcileMutate,
  }),
}))

const { LaunchCycle } = await import('./LaunchCycle')

// Roadmap à socle interactif + (optionnel) une feature de travail authorée (interview produite).
function roadmap({ socleClosed = false, socleMerged = false, hasWork = true } = {}): Roadmap {
  const features: unknown[] = [
    { slug: 'socle-design', status: socleMerged ? 'merged' : 'active', next: null,
      tasks: [{ slug: 'cadrage', status: socleClosed ? 'done' : 'todo', mode: 'interactive' }] },
  ]
  if (hasWork)
    features.push({ slug: 'build', status: 'active', next: 'impl',
      tasks: [{ slug: 'impl', status: 'todo', mode: 'headless' }] })
  return { project: 'atlas', features } as unknown as Roadmap
}

beforeEach(() => {
  h.checkOk = true
  h.reconcileData = undefined
  h.reconcileMutate.mockClear()
  h.reconcilePending = false
})

describe('LaunchCycle — légibilité du cycle de lancement', () => {
  it('projet SANS socle interactif → ne rend rien (aucun cycle à montrer)', () => {
    const mature = { project: 'atlas', features: [
      { slug: 'build', status: 'active', next: 'impl',
        tasks: [{ slug: 'impl', status: 'todo', mode: 'headless' }] }] } as unknown as Roadmap
    const { container } = render(<LaunchCycle project="atlas" roadmap={mature} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('interview produite + roadmap verte + socle ouvert → action nommée par son résultat, qui déclenche la réconciliation', () => {
    render(<LaunchCycle project="atlas" roadmap={roadmap()} />)
    const btn = screen.getByRole('button', { name: /Valider l'interview & clôturer le socle/i })
    fireEvent.click(btn)
    expect(h.reconcileMutate).toHaveBeenCalledTimes(1)
  })

  it('roadmap PAS verte (interview inachevée) → pas encore d\'action de clôture', () => {
    h.checkOk = false
    render(<LaunchCycle project="atlas" roadmap={roadmap()} />)
    expect(screen.queryByRole('button', { name: /clôturer le socle/i })).toBeNull()
  })

  it('après réconciliation → compte-rendu HUMAIN clair (sha du design, N tasks closes, prochaine étape)', () => {
    h.reconcileData = {
      status: 'reconciled', completed: true, feature: 'socle-design', design_sha: 'abcdef1234',
      socle_tasks_closed: 2, issues: [], next_step: 'Drain des features de travail (`forgemaster run`).',
    }
    render(<LaunchCycle project="atlas" roadmap={roadmap({ socleClosed: true })} />)
    const alertText = screen.getByText('Socle réconcilié').closest('[role="alert"]')?.textContent ?? ''
    expect(alertText).toContain('abcdef12')                 // sha court du design committé
    expect(alertText).toContain('2 task')                   // N tasks socle → done
    expect(alertText).toContain('Drain des features de travail')   // prochaine étape en clair
  })
})
