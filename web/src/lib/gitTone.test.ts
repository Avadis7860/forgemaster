import { describe, expect, it } from 'vitest'
import { gitBranchTone, syncTone } from './statusTone'

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

describe('syncTone', () => {
  it('mappe chaque état de sync miroir sur son ton sémantique', () => {
    expect(syncTone('synced')).toBe('ok')
    expect(syncTone('local_ahead')).toBe('info')
    expect(syncTone('remote_ahead')).toBe('warn')
    expect(syncTone('diverged')).toBe('danger')
  })

  it('garde les dégradations NON vertes (pas de faux-vert) et replie sur neutral', () => {
    // injoignable / pas-de-miroir ne doivent JAMAIS être `ok` (vert) — c'est l'invariant anti-faux-vert
    expect(syncTone('unreachable')).toBe('neutral')
    expect(syncTone('no_mirror')).toBe('neutral')
    expect(syncTone('inconnu')).toBe('neutral')
  })
})
