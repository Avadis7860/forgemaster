import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// La pastille a TROIS états et pas deux. Le troisième — « il a démarré mais ne peut rien servir » — est
// exactement celui qui était indistinguable du vert : une instance dont la base est illisible répondait 200.
// L'utilisateur n'a pas de terminal ; si le produit ne le dit pas ici, personne ne le lui dit.
const h = vi.hoisted(() => ({
  health: { data: undefined as unknown, isError: false, isPending: false },
}))

vi.mock('@/lib/queries', () => ({ useHealth: () => h.health }))

import { HealthDot } from './HealthDot'

describe('HealthDot', () => {
  beforeEach(() => {
    h.health = { data: undefined, isError: false, isPending: false }
  })

  it('dit la version quand l’instance sert', () => {
    h.health.data = { status: 'ok', version: '0.1.0', ready: true, detail: '' }
    render(<HealthDot />)
    expect(screen.getByText('daemon v0.1.0')).toBeInTheDocument()
  })

  it('dit INSERVABLE — et porte le motif — quand elle a démarré sans pouvoir servir', () => {
    h.health.data = {
      status: 'unservable', version: '0.1.0', ready: false,
      detail: 'cette base porte le schéma 21 → `forgemaster snapshot restore <instantané>`',
    }
    render(<HealthDot />)
    expect(screen.getByText('daemon inservable')).toBeInTheDocument()
    // Le motif est ATTEIGNABLE, pas seulement reçu : sans lui la pastille dirait « c'est cassé » sans dire
    // par quel geste on en sort, ce qui est le défaut qu'on corrige, pas sa moitié.
    expect(screen.getByTitle(/snapshot restore/)).toBeInTheDocument()
  })

  it('distingue « injoignable » d’« inservable » — deux états, deux causes', () => {
    h.health.isError = true
    render(<HealthDot />)
    expect(screen.getByText('daemon injoignable')).toBeInTheDocument()
  })
})
