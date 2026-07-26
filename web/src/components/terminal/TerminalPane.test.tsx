import { describe, expect, it } from 'vitest'
import { parseControlFrame } from './TerminalPane'

// Les frames de contrôle sont le contrat WS texte serveur→client. `session` décide si le client rejoue son
// `initialCommand` (handoff interview) — UNIQUEMENT sur une session neuve (gate `fresh`) ; `exit` porte la raison
// de fin du PTY (l'état de fin d'interview branche dessus). La sortie PTY, elle, est TOUJOURS binaire → tout texte
// est un contrôle. Ces tests verrouillent le parsing (et le fait qu'un `t:` inconnu est IGNORÉ, pas réécrit brut).
describe('parseControlFrame', () => {
  it('reconnaît une session neuve (fresh:true → le client rejoue initialCommand)', () => {
    expect(parseControlFrame(JSON.stringify({ t: 'session', fresh: true }))).toEqual({
      kind: 'session',
      fresh: true,
    })
  })

  it('reconnaît une ré-attache (fresh:false → surtout pas de re-run)', () => {
    expect(parseControlFrame(JSON.stringify({ t: 'session', fresh: false }))).toEqual({
      kind: 'session',
      fresh: false,
    })
  })

  it('normalise fresh en booléen (absent → false)', () => {
    expect(parseControlFrame(JSON.stringify({ t: 'session' }))).toEqual({ kind: 'session', fresh: false })
  })

  it('reconnaît une frame exit (failed_start → branche danger côté UI)', () => {
    expect(parseControlFrame(JSON.stringify({ t: 'exit', code: 127, reason: 'failed_start' }))).toEqual({
      kind: 'exit',
      code: 127,
      reason: 'failed_start',
    })
  })

  it('reconnaît une frame exit propre (clean, code 0)', () => {
    expect(parseControlFrame(JSON.stringify({ t: 'exit', code: 0, reason: 'clean' }))).toEqual({
      kind: 'exit',
      code: 0,
      reason: 'clean',
    })
  })

  it('durcit une raison exit inconnue en crash (et un code absent en null)', () => {
    expect(parseControlFrame(JSON.stringify({ t: 'exit', reason: 'wat' }))).toEqual({
      kind: 'exit',
      code: null,
      reason: 'crash',
    })
  })

  it('ignore une frame de contrôle à `t:` inconnu (kind:unknown → PAS réécrite brut dans le terminal)', () => {
    expect(parseControlFrame(JSON.stringify({ t: 'future', foo: 1 }))).toEqual({ kind: 'unknown' })
  })

  it('ignore la sortie PTY texte brute (non-JSON) → null (écrite telle quelle dans le terminal)', () => {
    expect(parseControlFrame('$ ls -la\n')).toBeNull()
  })

  it('un JSON sans champ `t` → null (fallback texte brut)', () => {
    expect(parseControlFrame(JSON.stringify({ type: 'resize', cols: 80 }))).toBeNull()
  })
})
