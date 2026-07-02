import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Card } from './Card'

describe('Card', () => {
  it('rend un div par défaut', () => {
    const { container } = render(<Card>contenu</Card>)
    expect(container.firstChild?.nodeName).toBe('DIV')
  })

  it('respecte la prop polymorphe `as`', () => {
    const { container } = render(<Card as="li">élément</Card>)
    expect(container.firstChild?.nodeName).toBe('LI')
  })
})
