import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FileDrop } from './FileDrop'

describe('FileDrop', () => {
  it('montre le placeholder sans fichier, le nom une fois posé', () => {
    const { rerender } = render(<FileDrop file={null} onFile={() => {}} />)
    expect(screen.getByText(/clique pour choisir/i)).toBeInTheDocument()
    const f = new File(['x'], 'charte.pdf', { type: 'application/pdf' })
    rerender(<FileDrop file={f} onFile={() => {}} />)
    expect(screen.getByText('charte.pdf')).toBeInTheDocument()
  })

  it('remonte le fichier sélectionné via le sélecteur natif', () => {
    const onFile = vi.fn()
    const { container } = render(<FileDrop file={null} onFile={onFile} />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    const f = new File(['data'], 'logo.png', { type: 'image/png' })
    fireEvent.change(input, { target: { files: [f] } })
    expect(onFile).toHaveBeenCalledWith(f)
  })

  it('remonte le fichier déposé (drag-drop)', () => {
    const onFile = vi.fn()
    render(<FileDrop file={null} onFile={onFile} />)
    const zone = screen.getByText(/clique pour choisir/i).closest('label') as HTMLLabelElement
    const f = new File(['data'], 'stamp.svg', { type: 'image/svg+xml' })
    fireEvent.drop(zone, { dataTransfer: { files: [f] } })
    expect(onFile).toHaveBeenCalledWith(f)
  })
})
