// updateLiaison — ce que le panneau de MAJ raconte quand son propre backend meurt.
//
// LA QUESTION DE CETTE SURFACE, et elle n'a pas d'équivalent ailleurs dans le produit : le daemon qui sert
// cette page est celui que la mise à jour arrête et remplace. Une UI naïve affiche « erreur » à la seconde
// exacte où tout se passe BIEN — parce qu'elle traite un `fetch` qui échoue comme une panne, alors que c'est
// l'issue attendue du geste qu'on vient de demander.
//
// D'où ce cœur PUR : la survie d'une UI ne se décide pas au milieu du JSX, elle se teste à la table. La
// symétrie avec le backend est exacte — `run_state` relit tout du disque parce que rien ne survit en
// mémoire côté serveur ; ici rien n'est retenu côté client non plus, on ne fait que qualifier un silence.
//
// Quatre états, et le quatrième est celui qui empêche les trois autres de mentir : un daemon muet SANS
// geste en vol est un daemon muet, pas une bascule. Maquiller ce cas rendrait toute la surface suspecte.

export type EtatLiaison = 'servie' | 'bascule' | 'perdue' | 'injoignable'

export interface EntreeLiaison {
  /** La dernière requête n'a pas abouti (au sens réseau : `ApiError` de statut 0). */
  muette: boolean
  /** Un geste est parti et n'a pas encore de verdict — le seul fait qui rend un silence explicable. */
  gesteEnVol: boolean
  /** Âge de la dernière réponse REÇUE, en ms. `null` = on n'en a jamais eu (on ne prétend pas d'âge). */
  depuisMs: number | null
  /** Borne du produit lui-même (`follow_timeout`, en ms), lue de la route. `null` = pas encore connue —
   *  et dans ce cas on n'escalade JAMAIS : annoncer un dépassement suppose de connaître la borne. */
  borneMs: number | null
}

export interface Liaison {
  etat: EtatLiaison
  titre: string
  detail: string
}

export function liaison({ muette, gesteEnVol, depuisMs, borneMs }: EntreeLiaison): Liaison {
  if (!muette) return { etat: 'servie', titre: '', detail: '' }

  if (!gesteEnVol) {
    return {
      etat: 'injoignable',
      titre: 'Daemon injoignable',
      detail:
        "Aucun geste de mise à jour n'est parti d'ici : ce silence n'est pas une bascule. " +
        "Cette page se reconnectera d'elle-même dès que l'instance répondra.",
    }
  }

  const depuis = depuisMs === null ? '' : ` Sans réponse depuis ${dureeFr(depuisMs)}.`

  if (borneMs !== null && depuisMs !== null && depuisMs > borneMs) {
    return {
      etat: 'perdue',
      titre: "L'instance aurait dû revenir",
      detail:
        `${depuis.trim()} C'est au-delà de la borne que le produit s'accorde lui-même ` +
        `(${dureeFr(borneMs)}) — ce panneau ne peut donc plus rien affirmer sur ce geste. ` +
        "Le verdict, lui, est écrit sur le disque de l'instance : il s'affichera ici dès qu'elle " +
        'répondra, sans rien relancer.',
    }
  }

  return {
    etat: 'bascule',
    titre: "L'instance ne répond plus — c'est attendu",
    detail:
      "Elle s'arrête, se remplace, puis revient. Cette page se reconnecte seule : il n'y a rien à " +
      `faire, et rien ne sera perdu si tu la recharges.${depuis}`,
  }
}

/** Durée lisible, en français, sans dépendance. Bornée au format le plus grossier qui reste vrai : on ne
 *  dit pas « 900 s » quand on peut dire « 15 min », et on ne dit pas « 0 min » quand il s'agit de 30 s. */
export function dureeFr(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s} s`
  const min = Math.floor(s / 60)
  const reste = s % 60
  if (min < 60) return reste === 0 ? `${min} min` : `${min} min ${reste} s`
  const h = Math.floor(min / 60)
  const minReste = min % 60
  return minReste === 0 ? `${h} h` : `${h} h ${minReste} min`
}

/** Un run est « en vol » quand on ATTEND encore quelque chose de lui. `unknown` en fait partie, et c'est
 *  délibéré : c'est un aveu du serveur (« je n'ai pas sondé »), pas une conclusion — le traiter comme fini
 *  reviendrait à conclure à sa place. `interrupted` et `never_started` n'en font PAS partie : le serveur a
 *  répondu et a tranché, il n'y a plus rien à attendre. `null` (aucun run) n'est pas un vol. */
export function enVol(etat: string | null | undefined): boolean {
  return etat === 'running' || etat === 'unknown'
}
