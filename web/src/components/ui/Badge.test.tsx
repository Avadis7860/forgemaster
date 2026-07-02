import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Badge } from './Badge'

describe('Badge', () => {
  it('applique les classes littérales du ton (source unique statusTone)', () => {
    render(<Badge tone="accent">READY</Badge>)
    expect(screen.getByText('READY').className).toContain('text-accent-400')
  })

  it('affiche une pastille quand dot est vrai', () => {
    const { container } = render(<Badge tone="danger" dot>CYCLE</Badge>)
    expect(container.querySelector('.bg-danger-500')).not.toBeNull()
  })
})
