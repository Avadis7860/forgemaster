import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Roadmap } from '@/lib/schemas'

// Holder hoisté : pilote la roadmap (source unique serveur), le gate de complétude et la navigation par test.
const h = vi.hoisted(() => ({
  roadmapData: undefined as Roadmap | undefined,
  checkOk: true,
  navigate: vi.fn(),
}))

vi.mock('@/lib/queries', () => ({
  useRoadmap: () => ({ data: h.roadmapData }),
  useRoadmapCheck: () => ({ data: { ok: h.checkOk } }),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => h.navigate }))

const { InterviewEndState } = await import('./InterviewEndState')

function roadmap({ hasWork = true, socleClosed = false } = {}): Roadmap {
  const features: unknown[] = [
    {
      slug: 'socle-design',
      status: 'active',
      next: null,
      tasks: [{ slug: 'cadrage', status: socleClosed ? 'done' : 'todo', mode: 'interactive' }],
    },
  ]
  if (hasWork)
    features.push({
      slug: 'build',
      status: 'active',
      next: 'impl',
      tasks: [{ slug: 'impl', status: 'todo', mode: 'headless' }],
    })
  return { project: 'atlas', features } as unknown as Roadmap
}

beforeEach(() => {
  h.roadmapData = undefined
  h.checkOk = true
  h.navigate.mockClear()
})

describe('InterviewEndState — fin d\'interview branchée sur le vrai statut du socle', () => {
  it('roadmap pas encore chargée → ne rend rien (pas d\'affirmation sans vérité serveur)', () => {
    const { container } = render(<InterviewEndState project="atlas" onReconnect={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('projet sans socle interactif → ne rend rien (fallback générique de fermeture)', () => {
    h.roadmapData = {
      project: 'atlas',
      features: [
        { slug: 'build', status: 'active', next: 'impl', tasks: [{ slug: 'impl', status: 'todo', mode: 'headless' }] },
      ],
    } as unknown as Roadmap
    const { container } = render(<InterviewEndState project="atlas" onReconnect={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('interview INCOMPLÈTE (roadmap pas verte) → « Reprendre l\'interview » appelle onReconnect, sans naviguer', () => {
    h.checkOk = false
    h.roadmapData = roadmap()
    const onReconnect = vi.fn()
    render(<InterviewEndState project="atlas" onReconnect={onReconnect} />)
    fireEvent.click(screen.getByRole('button', { name: /Reprendre l'interview/i }))
    expect(onReconnect).toHaveBeenCalledTimes(1)
    expect(h.navigate).not.toHaveBeenCalled()
  })

  it('socle CLOS → libellé « socle clos » + « Continuer le lancement » navigue vers l\'Accueil', () => {
    h.roadmapData = roadmap({ socleClosed: true })
    render(<InterviewEndState project="atlas" onReconnect={() => {}} />)
    expect(screen.getByText(/socle clos/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Continuer le lancement/i }))
    expect(h.navigate).toHaveBeenCalledWith({ to: '/$project', params: { project: 'atlas' } })
  })

  it('design prêt mais socle pas encore clos (réconciliation à venir) → productif, ouvre le lancement', () => {
    h.roadmapData = roadmap() // checkOk true (défaut) + socle ouvert → étape Réconciliation
    render(<InterviewEndState project="atlas" onReconnect={() => {}} />)
    expect(screen.getByText(/design prêt/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Continuer le lancement/i }))
    expect(h.navigate).toHaveBeenCalledTimes(1)
  })
})
