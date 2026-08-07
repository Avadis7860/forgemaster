import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RunFollow } from './RunFollow'
import type { UpdateRun } from '@/lib/schemas'

function run(over: Partial<UpdateRun> = {}): UpdateRun {
  return {
    run: '2026-08-07T10-00-00Z',
    mode: 'apply',
    scope: 'user',
    unit: 'forgemaster-update-2026-08-07T10-00-00Z',
    started_at: '2026-08-07T10:00:00Z',
    target: '/home/demo/.forgemaster/wheels/2026-08-07T09-59-00Z/exemple-0.1.0-py3-none-any.whl',
    state: 'failed',
    rc: 1,
    verdict: 'MAJ refusée — le vivant ne sert pas',
    impact: "aucun : le service n'a pas été touché",
    journal: '',
    ...over,
  }
}

describe('RunFollow — le verdict ET le périmètre', () => {
  it("affiche ce qui a bougé, parce que le verdict seul laisse la question ouverte", () => {
    // « MAJ refusée — le vivant ne sert pas » ne dit PAS si le service a été touché. Sans `impact`, qui n'a
    // pas de terminal doit le deviner — c'est exactement ce que cette surface existe pour éviter.
    render(<RunFollow run={run()} />)
    expect(screen.getByText(/MAJ refusée/)).toBeInTheDocument()
    expect(screen.getByText(/le service n'a pas été touché/)).toBeInTheDocument()
  })

  it("ne prétend RIEN sur ce qui a bougé quand le serveur ne l'a pas dit", () => {
    // Contre-témoin : `impact: null` veut dire « je n'en sais rien ». Afficher une ligne vide (ou pire, un
    // « aucun » par défaut) inventerait une réassurance que personne n'a mesurée.
    render(<RunFollow run={run({ state: 'interrupted', rc: null, impact: null,
      verdict: "parti, jamais conclu — l'unité n'est plus là" })} />)
    expect(screen.getByText(/jamais conclu/)).toBeInTheDocument()
    expect(screen.queryByText(/Ce qui a bougé/)).not.toBeInTheDocument()
  })

  it('nomme le geste par ce qu\'il est, aller ou retour', () => {
    render(<RunFollow run={run({ mode: 'rollback', state: 'done', rc: 0, verdict: 'retour arrière effectué' })} />)
    // Le libellé porte le sens de marche ET l'état : « retour effectué », pas « posée ». Et l'en-tête dit
    // simplement s'il s'agit du dernier geste ou d'un geste en cours — le titre de section dit déjà « MAJ ».
    expect(screen.getByText('Dernier geste')).toBeInTheDocument()
    expect(screen.getByText('retour effectué')).toBeInTheDocument()
  })
})
