import { describe, expect, it } from 'vitest'
import { deployUrl } from './deployUrl'

describe('deployUrl', () => {
  it('compose le lien à partir du hostname courant + le port (pas du loopback stocké)', () => {
    // jsdom sert la page en http://localhost → le lien produit hérite du hostname du viewer, jamais 127.0.0.1.
    const url = deployUrl(5250)
    expect(url).toBe(`http://${window.location.hostname}:5250`)
    expect(url.endsWith(':5250')).toBe(true)
    expect(url.includes('127.0.0.1')).toBe(false)
  })
})
