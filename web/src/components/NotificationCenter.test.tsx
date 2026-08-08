import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Alert, Version } from '@/lib/schemas'

// Holder hoisté : `alerts` pilote le retour de `useAlerts` ; `ackMutate` est le spy de la mutation d'ack ;
// `version` pilote la DEUXIÈME source que ce centre lit (le fait d'instance).
const h = vi.hoisted(() => ({
  alerts: [] as Alert[],
  isError: false,
  ackMutate: vi.fn(),
  refetch: vi.fn(),
  version: null as unknown,
}))

// `Link` rendu en `<a>` porteur du deep-link — on assère la cible « click and go ». Les deux formes du
// centre passent par ici : celle d'une alerte (`to`+`params`+`search`) et celle de l'instance (`to` seul).
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, search, children, onClick }: {
    to: string
    params?: { project: string }
    search?: { feature: string }
    children: React.ReactNode
    onClick?: () => void
  }) => (
    <a
      data-testid="deeplink"
      data-to={to}
      data-project={params?.project}
      data-feature={search?.feature}
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
  useInstanceFreshness: () => ({ data: h.version }),
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

function mkVersion(over: Partial<Version> = {}): Version {
  return {
    version: '0.1.0', sha: 'ab12345def', committed_at: '2026-08-07T00:00:00Z',
    comparable: true, stale: false, behind_by: 0, missing_types: [],
    reference: '/home/u/projects/forgemaster/sot.git', head: 'ab12345def', ...over,
  }
}

const EN_RETARD = mkVersion({ stale: true, behind_by: 12, head: '9f3c1d2aaa' })

beforeEach(() => {
  h.alerts = []
  h.isError = false
  h.version = null
  h.ackMutate.mockClear()
  h.refetch.mockClear()
  window.localStorage.clear()
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

describe('NotificationCenter — la DEUXIÈME source : le fait d’instance', () => {
  // D'OÙ VIENT CE BLOC : arbitrage de bosse du 2026-08-02 (option B). Le centre agrège deux sources parce
  // qu'« une instance en retard » n'a ni projet ni feature — l'y faire entrer par `alerts` obligerait à
  // inventer un projet sentinelle et casserait dédup, titre, deep-link et résolution d'un coup.

  it('pousse le retard SANS naviguer, et le compte dans le badge', () => {
    h.version = EN_RETARD
    render(<NotificationCenter />)
    expect(screen.getByLabelText('Alertes (1)')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Alertes (1)'))
    expect(screen.getByText(/En retard de 12 commits/)).toBeInTheDocument()
    expect(screen.getByTestId('deeplink')).toHaveAttribute('data-to', '/settings')
  })

  it('agrège les deux sources dans le compteur sans TEINTER le badge', () => {
    // Le compteur agrège, la couleur suit la plus grave : un fait d'instance ne peut pas rendre le badge
    // rouge — le rouge reste réservé à ce qui BLOQUE le drain. Sinon « en retard de 12 » se lirait comme
    // un gate cassé.
    h.alerts = [mkAlert({ severity: 'blocker' })]
    h.version = EN_RETARD
    render(<NotificationCenter />)
    expect(screen.getByLabelText('Alertes (2)')).toBeInTheDocument()
    expect(document.querySelector('.bg-danger-500\\/15')).not.toBeNull()
  })

  it('ne crie pas plus fort que ce qu’il annonce quand SEULE l’instance parle', () => {
    // TROUVÉ EN REGARDANT LE RENDU sur VM 9311, pas en relisant le diff : le badge sortait en `warn`
    // au-dessus d'une ligne `info`. Une pastille dont la teinte dépasse son motif use la teinte pour
    // toutes les autres — c'est le même mécanisme que le faux-vert, dans l'autre sens.
    h.version = EN_RETARD
    render(<NotificationCenter />)
    expect(document.querySelector('.bg-info-500\\/15')).not.toBeNull()
    expect(document.querySelector('.bg-warn-500\\/15')).toBeNull()
  })

  it('reste MUET sur une instance à jour — aucune ligne, aucun compteur', () => {
    h.version = mkVersion()
    render(<NotificationCenter />)
    expect(screen.getByLabelText('Alertes (0)')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Alertes (0)'))
    expect(screen.queryByText(/En retard/)).toBeNull()
    expect(screen.getByText('Aucune alerte')).toBeInTheDocument()
  })

  it('reste MUET quand il ne PEUT PAS savoir', () => {
    // Le contre-témoin qui décide de la crédibilité du badge : un centre qui s'allume pour dire qu'il ne
    // sait pas est un centre qu'on apprend à ignorer. `comparable=false` est l'état NORMAL d'une install
    // publique — c'est le cas de la majorité des instances distribuées, pas un cas de coin.
    h.version = mkVersion({ comparable: false, stale: null, behind_by: null,
                            reference: null, head: null })
    render(<NotificationCenter />)
    expect(screen.getByLabelText('Alertes (0)')).toBeInTheDocument()
  })

  it('ne contredit pas « Aucune alerte » quand seule l’instance parle', () => {
    // Sans cette garde, le déroulant afficherait « Aucune alerte / Rien ne bloque le drain » JUSTE SOUS une
    // ligne visible, badge allumé — le centre se démentirait à l'écran.
    h.version = EN_RETARD
    render(<NotificationCenter />)
    fireEvent.click(screen.getByLabelText('Alertes (1)'))
    expect(screen.getByText(/En retard de 12 commits/)).toBeInTheDocument()
    expect(screen.queryByText('Aucune alerte')).toBeNull()
  })

  it('le ✕ range la ligne ET éteint le badge', () => {
    h.version = EN_RETARD
    render(<NotificationCenter />)
    fireEvent.click(screen.getByLabelText('Alertes (1)'))
    fireEvent.click(screen.getByRole('button', { name: /Ignorer/ }))
    expect(screen.queryByText(/En retard de 12 commits/)).toBeNull()
    expect(screen.getByLabelText('Alertes (0)')).toBeInTheDocument()
    expect(h.ackMutate).not.toHaveBeenCalled()   // rien à acquitter côté serveur : c'est un ÉTAT, pas un événement
  })

  it('le rappel REVIENT au commit suivant de la référence, sans rien à ré-armer', () => {
    // C'est le sens du choix de clé : on n'ignore pas « le rappel », on ignore CE retard-là. Ranger sans
    // clé transformerait un rappel en interrupteur définitif — et le produit deviendrait muet pour de bon.
    window.localStorage.setItem('forgemaster:instance-stale-dismissed', '9f3c1d2aaa')
    h.version = EN_RETARD
    const range = render(<NotificationCenter />)
    expect(screen.getByLabelText('Alertes (0)')).toBeInTheDocument()
    range.unmount()

    h.version = mkVersion({ stale: true, behind_by: 13, head: 'c0ffee1bbb' })
    render(<NotificationCenter />)
    expect(screen.getByLabelText('Alertes (1)')).toBeInTheDocument()
  })
})
