/** theme — palette xterm 16 couleurs du terminal, dérivée des tokens `@theme` (source UNIQUE, aucun hex
 *  dupliqué en TS). Module PUR (aucun import xterm/DOM) : `buildTheme` prend un lecteur de variable CSS
 *  injecté → testable sans navigateur, et réutilisé par TerminalPane avec `getComputedStyle`. */

/** ANSI 0-15 + rôles xterm → nom du token CSS. Les 8 normales (0-7) réutilisent les teintes de statut ;
 *  les 8 brights (8-15) viennent de la rampe `--color-term-bright-*` (index.css). */
export const TERM_TOKENS = {
  background: '--color-bg',
  foreground: '--color-fg',
  cursor: '--color-accent-400',
  cursorAccent: '--color-bg',
  selectionBackground: '--color-accent-700',
  // ANSI normales (0-7)
  black: '--color-surface',
  red: '--color-danger-500',
  green: '--color-ok-500',
  yellow: '--color-warn-500',
  blue: '--color-info-500',
  magenta: '--color-purple-500',
  cyan: '--color-accent-400',
  white: '--color-muted',
  // ANSI brights (8-15)
  brightBlack: '--color-term-bright-black',
  brightRed: '--color-term-bright-red',
  brightGreen: '--color-term-bright-green',
  brightYellow: '--color-term-bright-yellow',
  brightBlue: '--color-term-bright-blue',
  brightMagenta: '--color-term-bright-magenta',
  brightCyan: '--color-term-bright-cyan',
  brightWhite: '--color-term-bright-white',
} as const

export type TermThemeKey = keyof typeof TERM_TOKENS

/** Construit l'objet thème xterm en résolvant chaque token via `readVar`. Pur : `readVar` est injecté
 *  (DOM en prod, faux getter en test). Une variable absente/vide est omise → xterm garde son défaut. */
export function buildTheme(readVar: (name: string) => string): Record<string, string> {
  const theme: Record<string, string> = {}
  for (const [key, token] of Object.entries(TERM_TOKENS)) {
    const value = readVar(token).trim()
    if (value) theme[key] = value
  }
  return theme
}
