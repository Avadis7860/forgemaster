import { describe, expect, it } from 'vitest'
import { gitDownloadUrl, gitRawUrl } from './api'
import { ReliabilitySchema } from './schemas'

describe('gitRawUrl / gitDownloadUrl', () => {
  it('construit une URL relative same-origin vers l’endpoint bytes, params encodés', () => {
    expect(gitRawUrl('atlas', 'dev', 'src/app.py')).toBe(
      '/api/projects/atlas/git/raw?ref=dev&path=src%2Fapp.py',
    )
    expect(gitDownloadUrl('atlas', 'dev', 'src/app.py')).toBe(
      '/api/projects/atlas/git/download?ref=dev&path=src%2Fapp.py',
    )
  })

  it('encode les caractères spéciaux du projet, de la réf et du chemin (pas d’injection de query)', () => {
    expect(gitRawUrl('a b', 'feature/x', 'dir/é&=.txt')).toBe(
      '/api/projects/a%20b/git/raw?ref=feature%2Fx&path=dir%2F%C3%A9%26%3D.txt',
    )
  })
})

describe('ReliabilitySchema', () => {
  it('parse un payload représentatif (scope projet, champs extra ignorés)', () => {
    const r = ReliabilitySchema.parse({
      scope: 'project', project: 'atlas', n_merges_verts: 2, n_reverted: 1, n_refixed: 0,
      n_adverse: 1, n_held: 1, n_marked: 1, provisional: false, n_blocked_open: 1, taux: 0.5,
      blocked_features: [
        { feature_ref: 'atlas/design-system', feature: 'design-system', reason: 'Tier-1.5 rouge' },
      ],
      features: [
        { id: 'x', project: 'atlas', feature: 'corridor', feature_ref: 'atlas/corridor', sha: 'abc',
          human_go: true, outcome: 'reverted', suggested: null, note: 'ko',
          merged_at: 't', updated_at: 't', marked_at: 't' },
      ],
    })
    expect(r.taux).toBe(0.5)
    expect(r.provisional).toBe(false)
    expect(r.blocked_features?.[0].feature).toBe('design-system')
    expect(r.features?.[0].outcome).toBe('reverted')
  })

  it('accepte un taux null (honnête-vide) et un scope global sans features', () => {
    const r = ReliabilitySchema.parse({
      scope: 'global', n_merges_verts: 0, n_reverted: 0, n_refixed: 0, n_adverse: 0, n_held: 0,
      n_marked: 0, provisional: false, n_blocked_open: 0, taux: null, projects: [],
    })
    expect(r.taux).toBeNull()
  })
})
