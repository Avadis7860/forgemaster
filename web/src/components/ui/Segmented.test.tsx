import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Segmented } from './Segmented'

const OPTS = [
  { value: 'historique', label: 'Historique' },
  { value: 'fichiers', label: 'Fichiers' },
  { value: 'diff', label: 'Diff' },
] as const

describe('Segmented', () => {
  it('marque le segment actif et notifie le changement', () => {
    const onChange = vi.fn()
    render(<Segmented options={OPTS} value="historique" onChange={onChange} ariaLabel="Vue Git" />)
    expect(screen.getByRole('tab', { name: 'Historique' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Fichiers' })).toHaveAttribute('aria-selected', 'false')
    fireEvent.click(screen.getByRole('tab', { name: 'Fichiers' }))
    expect(onChange).toHaveBeenCalledWith('fichiers')
  })
})
