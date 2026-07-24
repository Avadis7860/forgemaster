import { describe, expect, it } from 'vitest'
import { deriveLaunchStage } from './launch'
import type { Roadmap } from './schemas'

// Roadmap à socle interactif + (optionnel) une feature de travail authorée — mêmes seuils que LaunchCycle.test.
function roadmap({ hasWork = true, socleClosed = false, socleMerged = false } = {}): Roadmap {
  const features: unknown[] = [
    {
      slug: 'socle-design',
      status: socleMerged ? 'merged' : 'active',
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

describe('deriveLaunchStage — dérivation partagée frise ⟂ état de fin d\'interview', () => {
  it('projet SANS socle interactif → current -1, socle undefined', () => {
    const mature = {
      project: 'atlas',
      features: [
        { slug: 'build', status: 'active', next: 'impl', tasks: [{ slug: 'impl', status: 'todo', mode: 'headless' }] },
      ],
    } as unknown as Roadmap
    const s = deriveLaunchStage(mature, true)
    expect(s.socle).toBeUndefined()
    expect(s.current).toBe(-1)
  })

  it('socle seul, aucune feature de travail → étape Interview (0)', () => {
    expect(deriveLaunchStage(roadmap({ hasWork: false }), false).current).toBe(0)
  })

  it('features authorées mais roadmap pas verte → étape Design (1)', () => {
    expect(deriveLaunchStage(roadmap(), false).current).toBe(1)
  })

  it('roadmap verte, socle encore ouvert → étape Réconciliation (2)', () => {
    const s = deriveLaunchStage(roadmap(), true)
    expect(s.socleClosed).toBe(false)
    expect(s.current).toBe(2)
  })

  it('socle clos, pas encore mergé → étape Socle mergé (3)', () => {
    const s = deriveLaunchStage(roadmap({ socleClosed: true }), true)
    expect(s.socleClosed).toBe(true)
    expect(s.current).toBe(3)
  })

  it('socle mergé → étape Features de travail (4)', () => {
    expect(deriveLaunchStage(roadmap({ socleClosed: true, socleMerged: true }), true).current).toBe(4)
  })
})
