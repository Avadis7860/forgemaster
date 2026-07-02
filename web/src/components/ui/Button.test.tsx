import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button'

describe('Button', () => {
  it('rend son label et déclenche onClick', () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Dispatcher</Button>)
    fireEvent.click(screen.getByRole('button', { name: 'Dispatcher' }))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('busy désactive et bloque le clic', () => {
    const onClick = vi.fn()
    render(<Button busy onClick={onClick}>X</Button>)
    const btn = screen.getByRole('button')
    expect(btn).toBeDisabled()
    fireEvent.click(btn)
    expect(onClick).not.toHaveBeenCalled()
  })
})
