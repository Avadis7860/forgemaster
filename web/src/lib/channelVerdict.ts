// channelVerdict — « une version existe-t-elle, et dois-je faire quelque chose ? », vu du canal servi.
//
// CE QUE CETTE SURFACE RÉPARE. `instanceFreshness` répond à la même question contre un MIROIR LOCAL — une
// référence qui vieillit AVEC l'instance, et qui est absente chez l'utilisateur distribué (`comparable:
// false`, honnête et muet). Le canal est la première référence JOIGNABLE : elle ne vieillit pas avec nous.
//
// LES DEUX PIÈGES, nommés parce qu'ils sont exactement ceux que le reste du cycle passe son temps à tuer :
//
// 1. `cannot-situate` n'est PAS une divergence. Trois causes produisent la même absence de la lignée —
//    instance plus ancienne que la fenêtre publiée, wheel bâti maison, divergence réelle. On ne les
//    distingue pas, donc on n'accuse pas : c'est un AVEU, et il ne rougit pas.
// 2. `unverified` (signature invalide, clé inconnue) est IGNORÉ côté produit — rien n'est proposé — mais
//    il est le plus bruyant de tous côté log. Le silence produit et le bruit log ne sont pas contradictoires :
//    l'un refuse d'agir sur des octets non authentifiés, l'autre refuse de les taire.
//
// Cœur PUR, même patron que `instanceFreshness` / `instanceEdition` / `updateLiaison` : les états se
// décident et se testent à la table, jamais au milieu du JSX.

import type { Tone } from '@/lib/statusTone'
import type { Channel, Version } from '@/lib/schemas'
import { fraicheur } from '@/lib/instanceFreshness'

export type EtatCanal = Channel['state']

export interface VerdictCanal {
  etat: EtatCanal
  ton: Tone
  /** Le verdict en deux ou trois mots — ce qui tient dans un badge. */
  etiquette: string
  /** La phrase du verdict. */
  titre: string
  /** Ce qui le rend jugeable : la raison rendue par le produit, jamais reformulée ici. */
  detail: string | null
  /** Le dernier contrôle, dit SEULEMENT quand il n'est pas déjà le sujet — sinon une cause unique
   *  passerait pour deux faits distincts. */
  tentative: string | null
  /** Le SEUL état qui a le droit d'allumer le badge du centre de notifications : une édition qui descend
   *  réellement de nous. Ni un aveu, ni une alarme de signature — un centre qui s'allume pour dire qu'il
   *  ne sait pas est un centre qu'on apprend à ignorer. */
  pousse: boolean
  /** La clé du rappel qu'on ignore : le SHA ANNONCÉ. Une nouvelle édition le change → le rappel revient.
   *  Symétrique de la clé `head` du rappel miroir, et pour la même raison. */
  cle: string | null
  /** Le canal a-t-il quelque chose de VÉRIFIÉ à dire ? C'est ce qui lui donne la priorité sur le miroir
   *  local dans le centre de notifications — pas son existence, sa capacité à avoir authentifié. */
  fait_autorite: boolean
}

const court = (sha: string) => sha.slice(0, 7)

/** Le dernier tour, quand il n'est PAS déjà le sujet du verdict — décidé par `from_attempt`, rendu par le
 *  produit, et non par une liste d'états recopiée ici (elle divergerait de celle de la CLI). */
function tentative(c: Channel): string | null {
  if (c.attempt.state === 'ok' || c.from_attempt) return null
  return `dernier contrôle : ${c.attempt.state}${c.attempt.reason ? ` — ${c.attempt.reason}` : ''}`
}

export function verdictCanal(c: Channel): VerdictCanal {
  const base = {
    etat: c.state,
    detail: c.reason || null,
    tentative: tentative(c),
    pousse: false,
    cle: c.announced?.sha ?? null,
    fait_autorite: false,
  }

  switch (c.state) {
    // Jamais interrogé. « Je n'ai pas regardé » n'est pas « rien n'existe », et les confondre serait le
    // faux-vert que tout ce cycle refuse.
    case 'never':
      return { ...base, ton: 'info', etiquette: 'jamais interrogé',
               titre: 'Aucune annonce vérifiée sur cette instance' }

    // Capacité ABSENTE, pas panne. Cette édition n'embarque pas de racine de confiance, donc elle
    // n'interroge rien du tout — elle jetterait la réponse de toute façon.
    case 'no-trust-root':
      return { ...base, ton: 'info', etiquette: 'canal indisponible',
               titre: 'Cette édition n\'embarque aucune racine de confiance — elle ne peut rien vérifier' }

    // La seule alarme. On n'agit sur rien, et on le DIT — un produit qui se tairait ici apprendrait à
    // l'utilisateur que l'alarme n'existe pas.
    case 'unverified':
      return { ...base, ton: 'danger', etiquette: 'non vérifiée',
               titre: 'Annonce NON VÉRIFIÉE — le manifeste servi n\'est pas authentifiable, rien n\'est proposé' }

    // Le réseau n'a rien donné et on n'avait rien d'avant. Ce n'est pas une panne du produit.
    case 'unreachable':
      return { ...base, ton: 'info', etiquette: 'injoignable',
               titre: 'Canal injoignable — rien n\'a encore pu être lu' }

    case 'up-to-date':
      return { ...base, ton: 'ok', fait_autorite: true, etiquette: 'à jour · canal',
               titre: `À jour avec l'édition annoncée${c.announced?.version ? ` (${c.announced.version})` : ''}` }

    case 'available':
      return { ...base, ton: 'warn', fait_autorite: true, pousse: true,
               etiquette: `mise à jour ${c.announced?.version ?? 'disponible'}`,
               titre: c.announced?.sha
                 ? `Une édition plus récente est annoncée — ${c.announced.version} @ ${court(c.announced.sha)}`
                 : 'Une édition plus récente est annoncée' }

    // L'AVEU. Ton neutre et non `danger` : le rouge dirait « tu as divergé », qui est précisément le
    // verdict qu'on n'a pas les moyens de rendre. Et `fait_autorite` est VRAI — le canal a authentifié
    // quelque chose, il a donc le droit de faire taire le rappel du miroir — mais `pousse` est FAUX :
    // il n'a rien à proposer.
    case 'cannot-situate':
      return { ...base, ton: 'info', fait_autorite: true, etiquette: 'non situable',
               titre: 'Édition annoncée non proposée — cette instance n\'est pas situable dans la lignée publiée' }
  }
}

/** La ligne d'instance du centre de notifications, source ARBITRÉE — et il n'y en a jamais qu'une. */
export interface RappelInstance {
  titre: string
  detail: string | null
  /** La clé du `✕` : un nouveau SHA la change, donc le rappel revient de lui-même. */
  cle: string | null
  source: 'canal' | 'miroir'
}

/**
 * Qui a le droit de pousser un rappel d'instance — le canal servi, ou le miroir local ? **Le canal, dès
 * qu'il fait autorité**, arbitrage de bosse du 2026-08-08.
 *
 * Le motif n'est pas hiérarchique mais factuel : le canal est la référence que l'utilisateur visé peut
 * réellement joindre, et elle ne vieillit pas avec son instance ; le miroir local est une commodité de
 * développement qui vieillit AVEC elle (deux périmés que le daemon voit égaux, donc « frais » — le cas est
 * mesuré et documenté côté banc E2E). Faire pousser les deux afficherait DEUX rappels pour le MÊME fait sur
 * toute machine qui a les deux — la nôtre, la VM de banc — et c'est exactement ainsi qu'on apprend à
 * ignorer un centre de notifications.
 *
 * « Faire autorité » n'est pas « exister » : c'est avoir authentifié quelque chose. Un canal muet, injoignable
 * ou dont la signature ne vérifie pas ne fait taire personne — le miroir reprend la parole, comme avant.
 * Et un canal qui fait autorité SANS avoir de quoi proposer (`cannot-situate`, `up-to-date`) fait taire le
 * miroir **sans rien pousser** : c'est le cas qui compte, parce qu'il empêche un « en retard » du miroir de
 * contredire un « je ne peux pas te situer » du canal, qui en sait pourtant davantage.
 *
 * Coût dit, pas caché : sur nos postes, le rappel miroir familier disparaît dès que le canal parle.
 *
 * Cœur PUR — l'arbitrage se teste à la table, jamais en montant deux composants pour l'observer.
 */
export function rappelInstance(v: Version): RappelInstance | null {
  const canal = verdictCanal(v.channel)
  if (canal.fait_autorite) {
    return canal.pousse
      ? { titre: canal.titre, detail: canal.detail, cle: canal.cle, source: 'canal' }
      : null
  }
  const f = fraicheur(v)
  return f.pousse ? { titre: f.titre, detail: f.detail, cle: f.head, source: 'miroir' } : null
}
