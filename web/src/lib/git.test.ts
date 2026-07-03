import { describe, expect, it } from 'vitest'
import { isLogUnified } from './git'
import type { GitAheadBehind } from './schemas'

const ab = (ahead: number, behind: number): GitAheadBehind => ({ base: 'main', head: 'dev', ahead, behind })

describe('isLogUnified', () => {
  it('fusionne quand dev==main et les deux réfs présentes', () => {
    expect(isLogUnified(ab(0, 0), 2)).toBe(true)
  })

  it('ne fusionne pas si divergence (dev en avance)', () => {
    expect(isLogUnified(ab(3, 0), 2)).toBe(false)
  })

  it('ne fusionne pas s’il n’y a qu’une réf, ou pas de comparaison', () => {
    expect(isLogUnified(ab(0, 0), 1)).toBe(false)
    expect(isLogUnified(null, 2)).toBe(false)
  })
})
