// instanceFreshness — « suis-je en retard ? », et les trois façons de ne pas pouvoir répondre.
//
// LE PIÈGE DE CETTE SURFACE : le seul état confortable à afficher (« à jour ») est aussi le seul qu'on n'a
// pas le droit d'écrire nu. `stale=false` veut dire « égal au HEAD de TON MIROIR LOCAL » — un miroir qui
// vieillit avec l'instance. Le cas typique est mesuré et documenté côté banc E2E : wheel ET miroir tous
// deux périmés, le daemon les voit égaux, donc « frais ». La référence est donc NOMMÉE dans la phrase, à
// chaque fois. Une référence joignable par le réseau (qui, elle, ne vieillit pas avec nous) appartient à la
// phase suivante ; ce module ne prétend pas l'avoir.
//
// Cœur PUR, même patron que `updateLiaison` : les quatre états se décident et se testent à la table, jamais
// au milieu du JSX. Et le quatrième (`incomparable`) est celui qui empêche les trois autres de mentir —
// sans lui, « pas en retard » et « je ne peux pas savoir » s'écriraient de la même façon.

import type { Tone } from '@/lib/statusTone'
import type { Version } from '@/lib/schemas'

export type EtatFraicheur = 'inconnue' | 'incomparable' | 'a-jour' | 'en-retard'

export interface Fraicheur {
  etat: EtatFraicheur
  ton: Tone
  /** Le verdict en deux ou trois mots — ce qui tient dans un badge, au poids de ses voisins de page.
   *  Il porte quand même son qualificatif : « à jour » **nu** promettrait plus que ce qui est mesuré. */
  etiquette: string
  /** La phrase du verdict. Nomme TOUJOURS la référence quand il y en a une. */
  titre: string
  /** Ce qui rend le verdict jugeable : où est la référence, à quel commit. `null` s'il n'y en a pas. */
  detail: string | null
  /** Les types de bundle apparus depuis le build — vide hors `en-retard`. */
  manquants: string[]
  /** Le SEUL état qui a le droit d'allumer le badge du centre de notifications. */
  pousse: boolean
  /** Le SHA de référence : clé du rappel qu'on ignore (un nouveau commit le change → le rappel revient). */
  head: string | null
}

const court = (sha: string) => sha.slice(0, 7)

/** Où et à quel commit — la moitié qui rend un verdict jugeable. `null` quand il n'y a rien à nommer. */
function reference(v: Version): string | null {
  if (!v.reference) return null
  return v.head ? `référence ${v.reference} · HEAD ${court(v.head)}` : `référence ${v.reference}`
}

export function fraicheur(v: Version): Fraicheur {
  const base = { detail: reference(v), manquants: [] as string[], pousse: false, head: v.head }

  // Aucun tampon de build : c'est un checkout de développement, pas un wheel bâti. On ne prétend rien —
  // même si la référence, elle, est parfaitement lisible (et on la dit quand même : elle situe l'instance).
  if (v.sha === null)
    return { ...base, etat: 'inconnue', ton: 'info', etiquette: 'build non tamponné',
             titre: 'Build non tamponné — cette instance vient d\'un checkout, pas d\'un wheel' }

  // Rien à comparer. NE JAMAIS replier ce cas sur « à jour » : c'est exactement le faux-vert que tout le
  // reste du cycle refuse. C'est aussi l'état NORMAL d'une install publique, pas une panne.
  const incomparable: Fraicheur = {
    ...base, etat: 'incomparable', ton: 'info', etiquette: 'non comparable',
    titre: v.reference
      ? 'Fraîcheur non comparable — la référence locale n\'a pas pu être lue'
      : 'Fraîcheur non comparable — aucune référence locale sur cette instance',
  }
  if (!v.comparable) return incomparable

  if (v.stale === false)
    return { ...base, etat: 'a-jour', ton: 'ok', etiquette: 'à jour · miroir local',
             titre: 'À jour avec ton miroir local — cette référence vieillit avec l\'instance' }

  // `comparable` annoncé, verdict ABSENT. Le backend ne produit pas ce couple aujourd'hui — et c'est
  // précisément pour ça qu'on l'écrit : le seul repli tentant (`stale !== true` ⇒ vert) ferait du jour où
  // le contrat glisse un jour de faux-vert silencieux. Un `null` ne verdit jamais.
  if (v.stale === null) return incomparable

  // En retard. `behind_by` peut être `null` alors même que le retard est certain : le build vient d'un
  // commit que le miroir ne connaît pas (commit local non poussé), donc le compte n'est pas calculable.
  // On DIT le retard sans inventer son ampleur — « de null » serait pire que pas de chiffre.
  const combien = v.behind_by === null
    ? 'En retard sur ton miroir local — de combien : non mesurable depuis ce build'
    : `En retard de ${v.behind_by} commit${v.behind_by > 1 ? 's' : ''} sur ton miroir local`
  return { ...base, etat: 'en-retard', ton: 'warn', titre: combien,
           etiquette: v.behind_by === null ? 'en retard' : `en retard de ${v.behind_by}`,
           manquants: v.missing_types, pousse: true }
}
