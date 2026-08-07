import { describe, expect, it } from 'vitest'
import { dureeFr, enVol, liaison } from './updateLiaison'

const BORNE = 900_000            // `follow_timeout` du daemon, en ms — lu de la route, jamais épinglé en prod

describe('liaison — ce que le panneau raconte quand son backend meurt', () => {
  it("ne qualifie rien tant que l'instance répond", () => {
    const l = liaison({ muette: false, gesteEnVol: true, depuisMs: 0, borneMs: BORNE })
    expect(l.etat).toBe('servie')
    expect(l.titre).toBe('')
  })

  it("appelle ATTENDU le silence d'une instance à qui l'on vient de demander de se remplacer", () => {
    // Le test qui dit exactement le contraire de ce qu'une UI naïve fait : ici, `fetch` a échoué et il ne
    // s'est rien passé d'anormal. Traiter ce cas en erreur afficherait « échec » à la seconde où tout va bien.
    const l = liaison({ muette: true, gesteEnVol: true, depuisMs: 4_000, borneMs: BORNE })
    expect(l.etat).toBe('bascule')
    expect(l.titre).toContain('attendu')
    expect(l.detail).toContain('4 s')
    expect(l.detail).toContain('reconnecte seule')
  })

  it('avoue le dépassement au lieu de faire tourner un sablier sans fin', () => {
    const l = liaison({ muette: true, gesteEnVol: true, depuisMs: BORNE + 1, borneMs: BORNE })
    expect(l.etat).toBe('perdue')
    expect(l.detail).toContain('15 min')                 // la borne est DITE, pas juste dépassée
    expect(l.detail).toContain('disque')                 // et le verdict, lui, survit quelque part
  })

  it("place la frontière SUR la borne du produit, pas à un chiffre choisi ici", () => {
    // Exactement la borne = encore attendu ; un ms de plus = on l'avoue. La borne vient de `follow_timeout`,
    // que la route rend précisément pour que ce module n'ait pas à la recopier.
    expect(liaison({ muette: true, gesteEnVol: true, depuisMs: BORNE, borneMs: BORNE }).etat).toBe('bascule')
    expect(liaison({ muette: true, gesteEnVol: true, depuisMs: BORNE + 1, borneMs: BORNE }).etat)
      .toBe('perdue')
  })

  it("n'escalade JAMAIS quand la borne n'est pas encore connue", () => {
    // Contre-témoin de la frontière : annoncer un dépassement suppose de connaître la borne. Sans elle, on
    // reste sur « attendu » — c'est moins précis, ce n'est pas faux.
    const l = liaison({ muette: true, gesteEnVol: true, depuisMs: 10 * BORNE, borneMs: null })
    expect(l.etat).toBe('bascule')
  })

  it('ne maquille pas un daemon mort en bascule quand aucun geste n\'est parti', () => {
    // LE test qui empêche les trois autres de mentir. Si ce cas passait pour une bascule, tout le panneau
    // deviendrait suspect : il dirait « c'est normal » à chaque fois que l'instance tombe.
    const l = liaison({ muette: true, gesteEnVol: false, depuisMs: 30_000, borneMs: BORNE })
    expect(l.etat).toBe('injoignable')
    expect(l.detail).toContain("n'est pas une bascule")
  })

  it("ne prétend aucun âge quand aucune réponse n'a jamais été reçue", () => {
    const l = liaison({ muette: true, gesteEnVol: true, depuisMs: null, borneMs: BORNE })
    expect(l.etat).toBe('bascule')
    expect(l.detail).not.toContain('Sans réponse depuis')
  })
})

describe('enVol — ce dont on attend encore quelque chose', () => {
  it("compte `unknown`, parce que c'est un aveu du serveur et non une conclusion", () => {
    expect(enVol('running')).toBe(true)
    expect(enVol('unknown')).toBe(true)
  })

  it('ne compte pas les états que le serveur a déjà tranchés', () => {
    expect(enVol('done')).toBe(false)
    expect(enVol('failed')).toBe(false)
    expect(enVol('interrupted')).toBe(false)      // « parti, jamais conclu » : il n'y a plus rien à attendre
    expect(enVol('never_started')).toBe(false)
    expect(enVol(null)).toBe(false)
  })
})

describe('dureeFr', () => {
  it('reste au format le plus grossier qui soit encore vrai', () => {
    expect(dureeFr(0)).toBe('0 s')
    expect(dureeFr(45_000)).toBe('45 s')
    expect(dureeFr(60_000)).toBe('1 min')
    expect(dureeFr(95_000)).toBe('1 min 35 s')
    expect(dureeFr(900_000)).toBe('15 min')
    expect(dureeFr(3_600_000)).toBe('1 h')
    expect(dureeFr(5_400_000)).toBe('1 h 30 min')
  })
})
