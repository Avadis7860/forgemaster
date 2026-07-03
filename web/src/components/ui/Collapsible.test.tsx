import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Collapsible } from './Collapsible'

describe('Collapsible', () => {
  it('masque le contenu par défaut, le révèle au clic', () => {
    render(
      <Collapsible title="Miroir & token">
        <p>secret-config</p>
      </Collapsible>,
    )
    expect(screen.queryByText('secret-config')).toBeNull()
    const trigger = screen.getByRole('button', { name: /Miroir & token/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    expect(screen.getByText('secret-config')).toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
  })

  it('defaultOpen affiche le contenu d’emblée', () => {
    render(
      <Collapsible title="Détail" defaultOpen>
        <p>ouvert</p>
      </Collapsible>,
    )
    expect(screen.getByText('ouvert')).toBeInTheDocument()
  })
})
