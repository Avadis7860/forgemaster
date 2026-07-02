import { describe, expect, it } from 'vitest'
import { wsUrl } from './ws'

describe('wsUrl', () => {
  it('construit une URL même-origine à partir du chemin (ws en http)', () => {
    // jsdom sert la page en http://localhost → schéma ws (wss seulement en https).
    const url = wsUrl('/ws/terminal/atlas-demo')
    expect(url.startsWith('ws://')).toBe(true)
    expect(url.endsWith('/ws/terminal/atlas-demo')).toBe(true)
    expect(url.includes(window.location.host)).toBe(true)
  })
})
