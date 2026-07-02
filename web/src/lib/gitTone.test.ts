import { describe, expect, it } from 'vitest'
import { gitBranchTone } from './statusTone'

describe('gitBranchTone', () => {
  it('distingue les réfs protégées (main=ok, dev=info) des features (accent)', () => {
    expect(gitBranchTone('main')).toBe('ok')
    expect(gitBranchTone('dev')).toBe('info')
    expect(gitBranchTone('feature/phase-2-git-view')).toBe('accent')
  })

  it('replie sur neutral pour toute autre réf', () => {
    expect(gitBranchTone('hotfix/x')).toBe('neutral')
    expect(gitBranchTone('')).toBe('neutral')
  })
})
