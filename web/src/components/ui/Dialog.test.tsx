import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Dialog } from './Dialog'

describe('Dialog', () => {
  it('rend le contenu quand ouvert et notifie la fermeture', () => {
    const onOpenChange = vi.fn()
    render(
      <Dialog open onOpenChange={onOpenChange} title="Git · demo" side="right">
        <p>corps du drawer</p>
      </Dialog>,
    )
    // radix pose role="dialog" + le titre accessible ; le corps est monté.
    expect(screen.getByRole('dialog', { name: 'Git · demo' })).toBeInTheDocument()
    expect(screen.getByText('corps du drawer')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Fermer' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('ne monte pas le contenu quand fermé', () => {
    render(
      <Dialog open={false} onOpenChange={() => {}} title="Flow" side="center">
        <p>corps du modal</p>
      </Dialog>,
    )
    expect(screen.queryByText('corps du modal')).not.toBeInTheDocument()
  })
})
