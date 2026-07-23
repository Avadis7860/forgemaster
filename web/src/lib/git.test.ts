import { describe, expect, it } from 'vitest'
import {
  fuzzyFilter, isLogUnified, isReconcilable, needsReconcile, reconcileActionLabel, reconcileOutcome,
  reconcilePlan, syncSummary, timeAgo,
} from './git'
import type { GitAheadBehind, GitReconcile, GitSync } from './schemas'

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

describe('reconcilePlan (preview dérivée de l’état, jamais un dry-run réseau)', () => {
  it('dérive l’action ff-only par branche : ff GitHub / push / bloqué / rien', () => {
    const data = sync('diverged', {
      dev: { ahead: 0, behind: 2, state: 'remote_ahead' },   // miroir en avance → ff local
      main: { ahead: 3, behind: 0, state: 'local_ahead' },   // SoT en avance → push miroir
      feat: { ahead: 1, behind: 1, state: 'diverged' },      // vraie divergence → bloqué
      old: { ahead: 0, behind: 0, state: 'synced' },         // rien à faire
    })
    const plan = Object.fromEntries(reconcilePlan(data).map((p) => [p.branch, p]))
    expect(plan.dev).toMatchObject({ label: 'ff depuis GitHub (+2)', actionable: true })
    expect(plan.main).toMatchObject({ label: 'pousser vers GitHub (+3)', actionable: true })
    expect(plan.feat).toMatchObject({ label: 'divergé — réconciliation manuelle', actionable: false })
    expect(plan.old).toMatchObject({ label: 'à jour', actionable: false })
  })
})

describe('isReconcilable / needsReconcile', () => {
  it('réconciliable ssi une branche est ff-able (avance d’un côté)', () => {
    expect(isReconcilable(sync('remote_ahead', { dev: { ahead: 0, behind: 1, state: 'remote_ahead' } }))).toBe(true)
    expect(isReconcilable(sync('local_ahead', { dev: { ahead: 1, behind: 0, state: 'local_ahead' } }))).toBe(true)
    // rollup divergé cross-branche mais chaque branche ff-able → réconciliable (granularité par-branche)
    expect(isReconcilable(sync('diverged', {
      dev: { ahead: 0, behind: 1, state: 'remote_ahead' }, main: { ahead: 1, behind: 0, state: 'local_ahead' },
    }))).toBe(true)
    // vraie divergence sur la branche → rien de ff-able
    expect(isReconcilable(sync('diverged', { dev: { ahead: 1, behind: 1, state: 'diverged' } }))).toBe(false)
    expect(isReconcilable(sync('synced'))).toBe(false)
  })

  it('n’expose le bouton que sur une divergence réelle (jamais sur synced/dégradé)', () => {
    expect(needsReconcile(sync('remote_ahead'))).toBe(true)
    expect(needsReconcile(sync('diverged'))).toBe(true)   // exposé pour EXPLIQUER le blocage manuel
    expect(needsReconcile(sync('synced'))).toBe(false)
    expect(needsReconcile(sync('no_mirror'))).toBe(false)
    expect(needsReconcile(sync('unreachable'))).toBe(false)
  })
})

describe('reconcileOutcome / reconcileActionLabel (résultat post-POST honnête)', () => {
  const rep = (over: Partial<GitReconcile>): GitReconcile => ({
    project: 'p', remote: 'mirror', fetched: true, actions: {}, changed: false, blocked: [],
    state: 'synced', ...over,
  })

  it('résume ce qui a bougé, et signale les blocages sans faux-succès', () => {
    expect(reconcileOutcome(rep({
      actions: { dev: { action: 'fast_forward' }, main: { action: 'pushed' } }, changed: true,
    }))).toBe('2 branche(s) réconciliée(s)')
    expect(reconcileOutcome(rep({
      actions: { dev: { action: 'blocked_diverged' } }, blocked: ['dev'], state: 'diverged',
    }))).toBe('1 branche(s) bloquée(s) — réconciliation manuelle requise')
    expect(reconcileOutcome(rep({ actions: { dev: { action: 'already_synced' } } }))).toBe('déjà synchronisé')
    expect(reconcileOutcome(rep({ fetched: false, state: 'no_mirror' }))).toBe('pas de miroir')
  })

  it('nomme chaque action appliquée', () => {
    expect(reconcileActionLabel('fast_forward')).toBe('rattrapé (ff)')
    expect(reconcileActionLabel('blocked_worktree')).toBe('bloqué (worktree actif)')
  })
})

describe('timeAgo', () => {
  const now = Date.parse('2026-07-23T12:00:00Z')
  const S = 1000, M = 60 * S, H = 60 * M, D = 24 * H
  const ago = (ms: number) => new Date(now - ms).toISOString()

  it('« à l\'instant » sous 60 s', () => {
    expect(timeAgo(ago(30 * S), now)).toBe("à l'instant")
  })

  it('minutes / heures / jours au plancher', () => {
    expect(timeAgo(ago(5 * M), now)).toBe('il y a 5 min')
    expect(timeAgo(ago(3 * H), now)).toBe('il y a 3 h')
    expect(timeAgo(ago(3 * D), now)).toBe('il y a 3 j')
  })

  it('mois puis années, pluriel correct', () => {
    expect(timeAgo(ago(65 * D), now)).toBe('il y a 2 mois')
    expect(timeAgo(ago(400 * D), now)).toBe('il y a 1 an')
    expect(timeAgo(ago(800 * D), now)).toBe('il y a 2 ans')
  })

  it('date non parsable → ISO brut (jamais un faux « à l\'instant »)', () => {
    expect(timeAgo('pas-une-date', now)).toBe('pas-une-date')
  })
})

describe('fuzzyFilter (palette « go to file »)', () => {
  const paths = ['src/app.py', 'src/lib/util.py', 'README.md', 'docs/architecture.md', 'tests/test_app.py']

  it('matche en subsequence (caractères dans l\'ordre, insensible à la casse)', () => {
    const r = fuzzyFilter(paths, 'apppy')
    expect(r).toContain('src/app.py')
    expect(r).toContain('tests/test_app.py')
    expect(r).not.toContain('README.md')   // pas de subsequence 'apppy'
  })

  it('un match compact remonte avant un match dispersé', () => {
    // 'appy' est plus contigu dans 'src/app.py' que dispersé dans 'tests/test_app.py'
    const r = fuzzyFilter(paths, 'appy')
    expect(r.indexOf('src/app.py')).toBeLessThan(r.indexOf('tests/test_app.py'))
  })

  it('requête vide → tous les chemins (bornés par limit)', () => {
    expect(fuzzyFilter(paths, '')).toHaveLength(paths.length)
    expect(fuzzyFilter(paths, '', 2)).toHaveLength(2)
  })

  it('aucun match → liste vide', () => {
    expect(fuzzyFilter(paths, 'zzzzz')).toEqual([])
  })
})
