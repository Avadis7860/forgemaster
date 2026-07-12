import { describe, expect, it } from 'vitest'
import { isLogUnified, syncSummary } from './git'
import type { GitAheadBehind, GitSync } from './schemas'

const ab = (ahead: number, behind: number): GitAheadBehind => ({ base: 'main', head: 'dev', ahead, behind })

const sync = (state: GitSync['state'], branches: GitSync['branches'] = {}): GitSync =>
  ({ project: 'p', remote: 'mirror', fetched: state !== 'no_mirror' && state !== 'unreachable', branches, state })

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

describe('syncSummary', () => {
  it('injecte le compte cumulé sur les états actionnables (à ff / à pousser)', () => {
    // GitHub en avance sur dev (behind) ET main (behind) → compte cumulé des `behind`
    expect(syncSummary(sync('remote_ahead', {
      dev: { ahead: 0, behind: 2, state: 'remote_ahead' },
      main: { ahead: 0, behind: 1, state: 'remote_ahead' },
    }))).toBe('GitHub +3')
    expect(syncSummary(sync('local_ahead', {
      dev: { ahead: 4, behind: 0, state: 'local_ahead' },
    }))).toBe('à pousser +4')
  })

  it('donne un label explicite à chaque état, sans « à jour » trompeur sur les dégradations', () => {
    expect(syncSummary(sync('synced'))).toBe('miroir à jour')
    expect(syncSummary(sync('diverged'))).toBe('divergé')
    expect(syncSummary(sync('unreachable'))).toBe('injoignable')
    expect(syncSummary(sync('no_mirror'))).toBe('pas de miroir')
  })
})
