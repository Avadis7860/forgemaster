import { describe, expect, it } from 'vitest'
import { parseSessionFrame } from './TerminalPane'

// La frame de session est le contrat qui décide si le client rejoue son `initialCommand` (handoff interview) :
// UNIQUEMENT sur une session neuve. Une ré-attache (retour sur l'onglet Ops) ne doit JAMAIS re-lancer la
// commande — d'où le gate `fresh`. Ces tests verrouillent ce contrat de parsing.
describe('parseSessionFrame', () => {
  it('reconnaît une session neuve (fresh:true → le client rejoue initialCommand)', () => {
    expect(parseSessionFrame(JSON.stringify({ t: 'session', fresh: true }))).toEqual({ fresh: true })
  })

  it('reconnaît une ré-attache (fresh:false → surtout pas de re-run)', () => {
    expect(parseSessionFrame(JSON.stringify({ t: 'session', fresh: false }))).toEqual({ fresh: false })
  })

  it('normalise fresh en booléen (absent → false)', () => {
    expect(parseSessionFrame(JSON.stringify({ t: 'session' }))).toEqual({ fresh: false })
  })

  it('ignore la sortie PTY texte brute (non-JSON) → null (écrite telle quelle dans le terminal)', () => {
    expect(parseSessionFrame('$ ls -la\n')).toBeNull()
  })

  it('ignore un JSON qui n’est pas une frame de session → null', () => {
    expect(parseSessionFrame(JSON.stringify({ type: 'resize', cols: 80 }))).toBeNull()
  })
})
