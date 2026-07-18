import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LeaveTerminalConfirm } from './LeaveTerminalConfirm'

describe('LeaveTerminalConfirm', () => {
  it('idle → rien à confirmer (aucun dialog rendu)', () => {
    render(<LeaveTerminalConfirm blocker={{ status: 'idle' }} />)
    expect(screen.queryByText('Quitter le terminal ?')).toBeNull()
  })

  it('blocked → confirme le départ ; « Quitter quand même » poursuit (proceed)', () => {
    const proceed = vi.fn()
    const reset = vi.fn()
    render(<LeaveTerminalConfirm blocker={{ status: 'blocked', proceed, reset }} />)
    expect(screen.getByText(/session terminale est active/)).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Quitter quand même' }))
    expect(proceed).toHaveBeenCalledTimes(1)
    expect(reset).not.toHaveBeenCalled()
  })

  it('blocked → « Rester » annule (reset), la session survit', () => {
    const proceed = vi.fn()
    const reset = vi.fn()
    render(<LeaveTerminalConfirm blocker={{ status: 'blocked', proceed, reset }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Rester' }))
    expect(reset).toHaveBeenCalledTimes(1)
    expect(proceed).not.toHaveBeenCalled()
  })
})
