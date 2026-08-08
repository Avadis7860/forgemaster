import { describe, expect, it } from 'vitest'
import { rappelInstance, verdictCanal } from './channelVerdict'
import { ChannelSchema } from './schemas'
import type { Channel, Version } from './schemas'

function c(over: Partial<Channel> = {}): Channel {
  return {
    state: 'available',
    reason: '',
    from_attempt: false,
    attempt: { state: 'ok', at: '2026-08-08T11:00:00Z', reason: '' },
    verified_at: '2026-08-08T11:00:00Z',
    announced: {
      version: '0.2.0', sha: 'f00ba12345', committed_at: '2026-08-08T09:00:00Z',
      wheel_name: 'forgemaster-0.2.0-py3-none-any.whl', wheel_sha256: 'b'.repeat(64), lineage_len: 2,
    },
    ...over,
  }
}

function v(canal: Channel, over: Partial<Version> = {}): Version {
  return {
    version: '0.1.0', sha: 'ab12345def', committed_at: '2026-08-07T00:00:00Z',
    comparable: true, stale: false, behind_by: 0, missing_types: [],
    reference: '/home/u/projects/forgemaster/sot.git', head: 'ab12345def',
    install: { mode: 'edition', reason: null },
    maps: [], edition: { edition_dir: null, reason: null, state: 'unknown', maps: [] },
    mcp: { topology: 'none', sha: null, endpoint: null, reason: null },
    channel: canal,
    ...over,
  }
}

describe('verdictCanal — sept issues, et aucune ne verdit par défaut', () => {
  it('propose une édition qui DESCEND réellement de nous, et elle seule', () => {
    const r = verdictCanal(c())
    expect(r.etat).toBe('available')
    expect(r.pousse).toBe(true)
    expect(r.titre).toMatch(/0\.2\.0 @ f00ba12/)
    // La clé du rappel est le SHA ANNONCÉ : une nouvelle édition la change, donc le `✕` ne peut pas
    // éteindre définitivement un canal qui a de nouveau quelque chose à dire.
    expect(r.cle).toBe('f00ba12345')
  })

  it('ne propose RIEN et n\'accuse RIEN quand l\'instance n\'est pas situable', () => {
    // LE cas qui porte toute la phase. Trois causes donnent exactement cette absence — instance plus
    // ancienne que la fenêtre publiée, wheel bâti maison, divergence réelle. Un ton `danger` dirait
    // « tu as divergé », c'est-à-dire le seul verdict que ces trois causes interdisent de rendre.
    const r = verdictCanal(c({ state: 'cannot-situate', reason: 'trois causes donnent cette absence' }))
    expect(r.etat).toBe('cannot-situate')
    expect(r.pousse).toBe(false)
    expect(r.ton).not.toBe('danger')
    expect(r.ton).not.toBe('warn')
    // Mais il fait AUTORITÉ : il a authentifié quelque chose, donc il a le droit de faire taire un miroir
    // local qui en sait moins que lui.
    expect(r.fait_autorite).toBe(true)
  })

  it('est BRUYANT à l\'écran quand rien n\'a pu être authentifié, et ne propose toujours rien', () => {
    const r = verdictCanal(c({ state: 'unverified', reason: 'les octets ne sont pas ceux qu\'on a lus' }))
    expect(r.ton).toBe('danger')
    expect(r.pousse).toBe(false)
    // Il ne fait PAS autorité : on n'a rien authentifié aujourd'hui, donc on ne fait taire personne.
    expect(r.fait_autorite).toBe(false)
    expect(r.titre).toMatch(/rien n'est proposé/)
  })

  it('distingue « jamais interrogé » de « injoignable » de « aucune clé embarquée »', () => {
    // Trois silences, trois réparations différentes. Les fondre obligerait l'utilisateur à deviner s'il
    // doit attendre, vérifier son réseau, ou mettre à jour son binaire.
    const muets = (['never', 'unreachable', 'no-trust-root'] as const).map(
      (s) => verdictCanal(c({ state: s, announced: null, verified_at: null })))
    expect(new Set(muets.map((r) => r.etiquette)).size).toBe(3)
    expect(muets.every((r) => !r.pousse && !r.fait_autorite)).toBe(true)
  })

  it('ne redit le dernier contrôle QUE lorsqu\'il n\'est pas déjà le sujet', () => {
    // Sur un verdict issu d'un succès, le tour raté du jour est une information de PLUS (« ce que tu lis
    // date d'hier »). Sur `unverified`, il EST le sujet — le répéter ferait passer une cause unique pour
    // deux faits distincts.
    const vieilli = verdictCanal(c({ attempt: { state: 'unreachable', at: 'x', reason: 'HTTP 503' } }))
    expect(vieilli.tentative).toMatch(/unreachable/)
    const alarme = verdictCanal(c({ state: 'unverified', from_attempt: true,
                                    attempt: { state: 'bad-signature', at: 'x', reason: 'octets' } }))
    expect(alarme.tentative).toBeNull()
  })
})

describe('rappelInstance — une seule ligne, et le canal l\'emporte dès qu\'il fait autorité', () => {
  it('fait TAIRE le miroir dès que le canal a authentifié quelque chose', () => {
    // Le décor est celui qui rendrait DEUX lignes sans l'arbitrage : un miroir local qui crie « en retard »
    // pendant que le canal, lui, sait qu'il ne peut pas situer l'instance. Le canal en sait davantage.
    const r = rappelInstance(v(c({ state: 'cannot-situate' }),
                               { stale: true, behind_by: 12, head: 'deadbeef' }))
    expect(r).toBeNull()
  })

  it('rend la ligne du CANAL quand il a une édition à proposer', () => {
    const r = rappelInstance(v(c(), { stale: true, behind_by: 12, head: 'deadbeef' }))
    expect(r?.source).toBe('canal')
    expect(r?.cle).toBe('f00ba12345')
  })

  it('rend la parole au miroir quand le canal est muet — le comportement d\'avant, intact', () => {
    // « Faire autorité » n'est pas « exister ». Un canal jamais interrogé, injoignable, ou dont la
    // signature ne vérifie pas ne fait taire personne : sans cette règle, brancher le canal aurait
    // SUPPRIMÉ un rappel qui marchait, ce qui serait une régression déguisée en fonctionnalité.
    for (const s of ['never', 'unreachable', 'no-trust-root', 'unverified'] as const) {
      const r = rappelInstance(v(c({ state: s }), { stale: true, behind_by: 12, head: 'deadbeef' }))
      expect(r?.source, s).toBe('miroir')
      expect(r?.cle, s).toBe('deadbeef')
    }
  })

  it('ne pousse rien quand ni l\'un ni l\'autre n\'a de fait avéré', () => {
    expect(rappelInstance(v(c({ state: 'up-to-date' })))).toBeNull()
    expect(rappelInstance(v(c({ state: 'never' }), { stale: false }))).toBeNull()
  })
})

describe('ChannelSchema — le contrat ne peut pas glisser en silence', () => {
  it('refuse un état inconnu plutôt que de le laisser tomber dans un `default` d\'affichage', () => {
    // Un nouvel état backend non déclaré ici doit CASSER au parse. Sans ça, il traverserait jusqu'au
    // rendu et y prendrait l'apparence du cas par défaut — c'est-à-dire, ici, un état rassurant.
    expect(ChannelSchema.safeParse({ ...c(), state: 'probablement-ok' }).success).toBe(false)
    expect(ChannelSchema.safeParse(c()).success).toBe(true)
  })
})
