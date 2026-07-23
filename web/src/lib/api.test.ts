import { describe, expect, it } from 'vitest'
import { gitDownloadUrl, gitRawUrl } from './api'

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
