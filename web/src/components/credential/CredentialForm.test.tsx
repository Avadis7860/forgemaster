import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CredentialForm } from './CredentialForm'

// On isole le composant de TanStack Query : la liaison n'est pas exercée ici (couverte côté backend),
// seul le RENDU backend-aware + l'invariant « aucune valeur de secret affichée » sont testés.
vi.mock('@/lib/queries', () => ({
  useLinkCredential: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useUnlinkCredential: () => ({ mutate: vi.fn(), isPending: false }),
}))

describe('CredentialForm', () => {
  it('voie fichier : le champ token est masqué (type password)', () => {
    render(<CredentialForm project="p" backend="file" linked={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Lier un token' }))
    expect(screen.getByLabelText('token à stocker')).toHaveAttribute('type', 'password')
  })

  it('voie BWS : le champ est un UUID en clair (type text), libellé BWS', () => {
    render(<CredentialForm project="p" backend="bws" linked={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Lier un secret BWS (UUID)' }))
    expect(screen.getByLabelText('UUID du secret BWS')).toHaveAttribute('type', 'text')
  })

  it('lié : montre la réf opaque (tronquée) + actions, jamais une valeur de secret', () => {
    render(<CredentialForm project="p" backend="file" linked linkedRef="abcdef1234567890" />)
    expect(screen.getByText(/réf abcdef12/)).toBeInTheDocument()
    expect(screen.queryByText(/abcdef1234567890/)).toBeNull() // réf tronquée, jamais en entier
    expect(screen.getByRole('button', { name: 'Délier' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remplacer' })).toBeInTheDocument()
  })

  it('compact : masque le badge « token lié » redondant (l’appelant l’affiche déjà)', () => {
    render(<CredentialForm project="p" backend="file" linked linkedRef="abc123" compact />)
    expect(screen.queryByText('token lié')).toBeNull()
    expect(screen.getByRole('button', { name: 'Délier' })).toBeInTheDocument()
  })
})
