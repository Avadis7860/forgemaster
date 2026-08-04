import { describe, expect, it } from 'vitest'
import { ptyPath, tokenProtocols, wsUrl } from './ws'

describe('wsUrl', () => {
  it('construit une URL même-origine à partir du chemin (ws en http)', () => {
    // jsdom sert la page en http://localhost → schéma ws (wss seulement en https).
    const url = wsUrl('/ws/terminal/atlas-demo')
    expect(url.startsWith('ws://')).toBe(true)
    expect(url.endsWith('/ws/terminal/atlas-demo')).toBe(true)
    expect(url.includes(window.location.host)).toBe(true)
  })
})

describe('ptyPath', () => {
  it('shell → route terminal ; interview → route interview dédiée', () => {
    expect(ptyPath('atlas-demo', 'shell')).toBe('/ws/terminal/atlas-demo')
    expect(ptyPath('atlas-demo', 'interview')).toBe('/ws/interview/atlas-demo')
  })
  it('URL-encode le projet', () => {
    expect(ptyPath('a/b', 'interview')).toBe('/ws/interview/a%2Fb')
  })
})

describe('tokenProtocols', () => {
  it('token présent → sous-protocole `forgemaster.token.<v>` (injecté au handshake WS)', () => {
    expect(tokenProtocols('abc123')).toEqual(['forgemaster.token.abc123'])
  })
  it('token absent (pas encore chargé) → undefined : le consommateur n’ouvre PAS le WS', () => {
    expect(tokenProtocols(undefined)).toBeUndefined()
  })
})
