import { describe, expect, it } from 'vitest'
import { buildTheme, TERM_TOKENS } from './theme'

// Getter factice : renvoie le NOM du token (→ on vérifie le mapping ANSI→token sans DOM ni CSS réel).
const echoVar = (name: string) => name

describe('terminal theme', () => {
  it('mappe les 16 couleurs ANSI (8 normales + 8 brights) + fond/texte/curseur', () => {
    const theme = buildTheme(echoVar)
    const ansi = [
      'black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white',
      'brightBlack', 'brightRed', 'brightGreen', 'brightYellow',
      'brightBlue', 'brightMagenta', 'brightCyan', 'brightWhite',
    ]
    for (const key of ansi) expect(theme[key]).toBeDefined()
    expect(theme.background).toBe('--color-bg')
    expect(theme.foreground).toBe('--color-fg')
    expect(theme.cursor).toBe('--color-accent-400')
  })

  it('les 8 brights viennent de la rampe --color-term-bright-*', () => {
    const theme = buildTheme(echoVar)
    for (const key of ['brightRed', 'brightGreen', 'brightYellow', 'brightBlue', 'brightMagenta', 'brightCyan']) {
      expect(theme[key]).toMatch(/^--color-term-bright-/)
    }
  })

  it('omet une variable absente/vide (xterm garde son défaut)', () => {
    const theme = buildTheme((name) => (name === TERM_TOKENS.red ? '' : '#123456'))
    expect(theme.red).toBeUndefined()
    expect(theme.green).toBe('#123456')
  })
})
