import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Alert } from '@/lib/schemas'

// Holder hoisté : `alerts` pilote le retour de `useAlerts` ; `ackMutate` est le spy de la mutation d'ack.
const h = vi.hoisted(() => ({
  alerts: [] as Alert[],
  isError: false,
  ackMutate: vi.fn(),
  refetch: vi.fn(),
}))

// `Link` rendu en `<a>` porteur du deep-link (to/params/search) — on assère la cible « click and go ».
vi.mock('@tanstack/react-router', () => ({
  Link: ({ params, search, children, onClick }: {
    params: { project: string }
    search: { feature: string }
    children: React.ReactNode
    onClick?: () => void
  }) => (
    <a
      data-testid="deeplink"
      data-project={params.project}
      data-feature={search.feature}
      onClick={onClick}
    >
      {children}
    </a>
  ),
}))

vi.mock('@/lib/queries', () => ({
  useAlerts: () => ({
    data: { alerts: h.alerts, count: h.alerts.length },
    isLoading: false,
    isError: h.isError,
    refetch: h.refetch,
  }),
  useAckAlert: () => ({ mutate: h.ackMutate, isPending: false }),
}))

const { NotificationCenter } = await import('./NotificationCenter')

function mkAlert(over: Partial<Alert> = {}): Alert {
  return {
    id: 'a1', project: 'atlas', feature_ref: 'atlas/corridor', feature: 'corridor',
    kind: 'gate_red', tier: 'tier1', severity: 'blocker', reason: 'Tier-1 : aucune revue',
    findings: ['Tier-1 : aucune revue sur le HEAD'], status: 'open',
    created_at: 't', updated_at: 't', resolved_at: null, ...over,
  }
}

beforeEach(() => {
  h.alerts = []
  h.isError = false
  h.ackMutate.mockClear()
  h.refetch.mockClear()
})

describe('NotificationCenter — centre d’alertes poussé', () => {
  it('affiche le compteur et, ouvert, la feature bloquée + son motif', () => {
    h.alerts = [mkAlert()]
    render(<NotificationCenter />)
    expect(screen.getByLabelText('Alertes (1)')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()                 // badge compteur
    fireEvent.click(screen.getByLabelText('Alertes (1)'))
    expect(screen.getByText('corridor')).toBeInTheDocument()
    expect(screen.getByText('Tier-1 : aucune revue')).toBeInTheDocument()
  })

  it('vide → EmptyState « Aucune alerte » (axe 7), pas de compteur', () => {
    render(<NotificationCenter />)
    expect(screen.queryByText('1')).toBeNull()
    fireEvent.click(screen.getByLabelText('Alertes (0)'))
    expect(screen.getByText('Aucune alerte')).toBeInTheDocument()
  })

  it('chaque alerte porte un deep-link « click and go » vers /{project}/travail?feature=<slug>', () => {
    h.alerts = [mkAlert()]
    render(<NotificationCenter />)
    fireEvent.click(screen.getByLabelText('Alertes (1)'))
    const link = screen.getByTestId('deeplink')
    expect(link).toHaveAttribute('data-project', 'atlas')
    expect(link).toHaveAttribute('data-feature', 'corridor')
  })

  it('« Ignorer » acquitte l’alerte (mutation ack sur son id)', () => {
    h.alerts = [mkAlert({ id: 'xyz' })]
    render(<NotificationCenter />)
    fireEvent.click(screen.getByLabelText('Alertes (1)'))
    fireEvent.click(screen.getByRole('button', { name: /Ignorer/ }))
    expect(h.ackMutate).toHaveBeenCalledWith('xyz')
  })

  it('erreur daemon → Alert de récupération avec « Réessayer » (axe 7)', () => {
    h.isError = true
    render(<NotificationCenter />)
    fireEvent.click(screen.getByLabelText('Alertes (0)'))
    fireEvent.click(screen.getByText('Réessayer'))
    expect(h.refetch).toHaveBeenCalled()
  })
})
