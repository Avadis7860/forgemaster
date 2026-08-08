import { describe, expect, it } from 'vitest'
import { fraicheur } from './instanceFreshness'
import type { Version } from './schemas'

function v(over: Partial<Version> = {}): Version {
  return {
    version: '0.1.0', sha: 'ab12345def', committed_at: '2026-08-07T00:00:00Z',
    comparable: true, stale: false, behind_by: 0, missing_types: [],
    reference: '/home/u/projects/forgemaster/sot.git', head: 'ab12345def', ...over,
  }
}

describe('fraicheur — quatre états, et jamais un « à jour » nu', () => {
  it('nomme la référence dans le verdict à jour', () => {
    // LE cas qu'on n'a pas le droit d'écrire nu : `stale=false` veut dire « égal au HEAD de ton miroir
    // LOCAL », et ce miroir vieillit avec l'instance. Sans la référence dans la phrase, l'utilisateur lit
    // une garantie que le produit ne peut pas donner.
    const f = fraicheur(v())
    expect(f.etat).toBe('a-jour')
    expect(f.titre).toMatch(/miroir local/)
    // L'étiquette tient dans un badge, mais elle porte son qualificatif : « à jour » NU dans une pastille
    // serait exactement la promesse qu'on refuse — c'est souvent la seule chose qu'on lit en diagonale.
    expect(f.etiquette).toMatch(/miroir local/)
    expect(f.etiquette.length).toBeLessThan(25)
    expect(f.pousse).toBe(false)
    expect(f.detail).toMatch(/sot\.git/)
    expect(f.detail).toMatch(/HEAD ab12345/)
  })

  it('dit le retard, son ampleur et les types apparus depuis', () => {
    const f = fraicheur(v({ stale: true, behind_by: 12, missing_types: ['site-vitrine'] }))
    expect(f.etat).toBe('en-retard')
    expect(f.titre).toMatch(/En retard de 12 commits/)
    expect(f.manquants).toEqual(['site-vitrine'])
    expect(f.pousse).toBe(true)
  })

  it('n\'écrit JAMAIS « de null » quand le retard est certain mais pas mesurable', () => {
    // Cas RÉEL du backend : le build vient d'un commit que le miroir ne connaît pas (commit local non
    // poussé), donc `ahead_behind` échoue et `behind_by` reste `null` — alors que `stale` est certain.
    const f = fraicheur(v({ stale: true, behind_by: null }))
    expect(f.etat).toBe('en-retard')
    expect(f.titre).toMatch(/non mesurable/)
    expect(f.titre).not.toMatch(/null|NaN|undefined/)
    expect(f.pousse).toBe(true)
  })

  it('accorde le singulier — un produit qui écrit « 1 commits » se lit comme un brouillon', () => {
    expect(fraicheur(v({ stale: true, behind_by: 1 })).titre).toMatch(/1 commit sur/)
  })

  it('ne replie PAS « je ne peux pas savoir » sur « à jour »', () => {
    // Le contre-témoin qui empêche les trois autres états de mentir : sans lui, une install publique (aucun
    // miroir) afficherait le même vert qu'une instance réellement à jour. C'est le faux-vert que tout le
    // reste du cycle refuse.
    const f = fraicheur(v({ comparable: false, stale: null, behind_by: null,
                            reference: null, head: null }))
    expect(f.etat).toBe('incomparable')
    expect(f.titre).toMatch(/aucune référence locale/)
    expect(f.titre).not.toMatch(/à jour/i)
    expect(f.ton).not.toBe('ok')
    expect(f.pousse).toBe(false)
    expect(f.detail).toBeNull()
  })

  it('distingue « pas de miroir » de « miroir illisible »', () => {
    // Les deux rendent `comparable=false`, et ce ne sont pas les mêmes réparations : l'un est l'état NORMAL
    // d'une install publique, l'autre est un miroir cassé sur ce disque. Le backend les sépare déjà
    // (`reference` non nulle mais `head` nul) ; les fondre ici perdrait ce qu'il a pris soin de dire.
    const f = fraicheur(v({ comparable: false, stale: null, behind_by: null, head: null }))
    expect(f.titre).toMatch(/n'a pas pu être lue/)
    expect(f.detail).toMatch(/sot\.git/)
  })

  it('ne prétend rien d\'un build non tamponné, et situe quand même l\'instance', () => {
    const f = fraicheur(v({ sha: null, comparable: false, stale: null, behind_by: null }))
    expect(f.etat).toBe('inconnue')
    expect(f.titre).toMatch(/checkout/)
    expect(f.pousse).toBe(false)
    expect(f.detail).toMatch(/HEAD ab12345/)   // la référence est lisible : la taire n'aiderait personne
  })

  it('ne verdit JAMAIS un verdict absent, même annoncé comparable', () => {
    // Couple que le backend ne produit pas AUJOURD'HUI. Il est écrit parce que le repli tentant
    // (`stale !== true` ⇒ vert) transformerait le jour où le contrat glisse en jour de faux-vert
    // silencieux — le pire des trois, puisqu'il supprime le doute qui aurait déclenché la vérification.
    const f = fraicheur(v({ comparable: true, stale: null, behind_by: null }))
    expect(f.etat).toBe('incomparable')
    expect(f.ton).not.toBe('ok')
    expect(f.pousse).toBe(false)
  })

  it('rend le head comme clé de rappel — c\'est lui qui fait revenir la ligne ignorée', () => {
    expect(fraicheur(v({ stale: true, behind_by: 2, head: '9f3c1d2aaa' })).head).toBe('9f3c1d2aaa')
  })
})
