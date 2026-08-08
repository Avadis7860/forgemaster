// instanceEdition — « quelle édition tourne ici ? », et les deux façons de ne pas pouvoir répondre.
//
// CE QUE CETTE SURFACE RÉPARE : le verdict de conformité des cartes ne vivait que dans `forgemaster
// toolchain check`. Une commande, un terminal — donc hors de portée de la personne pour qui tout ce cycle
// de mise à jour existe. Une instance pouvait servir des cartes qui ne sont pas celles de son édition sans
// qu'aucune surface ne le dise.
//
// LE PIÈGE EST LE MÊME QUE POUR LA FRAÎCHEUR, et il vaut la peine de le nommer deux fois : le seul état
// confortable (« conforme ») est le seul qu'on n'a pas le droit d'écrire par défaut. `non-verifiee` n'est
// PAS un vert atténué, c'est un aveu — et sans lui, « je n'ai rien pu comparer » et « tout est conforme »
// s'écriraient de la même façon.
//
// Cœur PUR, même patron que `instanceFreshness` / `updateLiaison` : les états se décident et se testent à
// la table, jamais au milieu du JSX.

import type { Tone } from '@/lib/statusTone'
import type { Version } from '@/lib/schemas'

export type EtatEdition = 'conforme' | 'derive' | 'non-verifiee' | 'sans-edition'

/** Une pièce de l'édition — le wheel, puis les 3 cartes hôte. */
export interface PieceEdition {
  nom: string
  sha: string | null
  /** Ce qui remplace le SHA quand il n'y en a pas. Jamais un tiret muet : une absence a une raison. */
  motif: string | null
  /** Conformité de CETTE pièce. `null` pour le wheel : il EST l'édition, il ne peut pas différer de
   *  lui-même — et lui inventer un état le ferait passer pour une pièce qu'on aurait oublié de vérifier. */
  etat: 'up-to-date' | 'differs' | 'unknown' | null
  /** Le SHA que l'édition DÉCLARE, quand il diffère de celui qui est servi. C'est LUI qui signale l'écart
   *  au rendu — pas une teinte par pièce, qui redirait la même chose sans rien ajouter. */
  attendu: string | null
}

export interface VerdictEdition {
  etat: EtatEdition
  ton: Tone
  /** Le verdict en deux ou trois mots — ce qui tient dans un badge. */
  etiquette: string
  /** La phrase du verdict. */
  titre: string
  /** Ce qui le rend jugeable : d'où vient le mode, ou pourquoi rien n'a pu être comparé. */
  detail: string | null
  /** Le geste qui remet à niveau. `null` quand il n'y a rien à réparer — jamais une commande décorative. */
  remede: string | null
  /** Les QUATRE pièces, wheel en tête. */
  pieces: PieceEdition[]
}

/** Le SHA court, tel qu'il s'affiche partout dans le panneau. Exporté parce que le rendu des pièces en a
 *  besoin et qu'une seconde troncature ailleurs finirait par ne plus couper au même endroit. */
export const shaCourt = (sha: string) => sha.slice(0, 7)

/** Le nom du mode, dit en français à l'endroit où on le lit. */
const MODE_LIBELLE: Record<Version['install']['mode'], string> = {
  edition: 'wheel d\'édition',
  wheel: 'wheel sans édition',
  checkout: 'checkout éditable',
  unknown: 'mode indéterminé',
}

/** Les 4 pièces, wheel en tête : la question « quelle édition tourne ici ? » porte sur l'ensemble, pas sur
 *  le seul binaire qui répond. Chaque carte reçoit la conformité que l'édition lui donne — et `unknown`
 *  quand l'édition ne dit rien d'elle, jamais rien du tout. */
function pieces(v: Version): PieceEdition[] {
  const parNom = new Map(v.edition.maps.map((m) => [m.name, m]))
  const wheel: PieceEdition = {
    nom: 'forgemaster',
    sha: v.sha,
    motif: v.sha === null ? v.install.reason : null,
    etat: null,
    attendu: null,
  }
  return [wheel, ...v.maps.map((m): PieceEdition => {
    const c = parNom.get(m.name)
    const etat = c?.state ?? 'unknown'
    return {
      nom: m.name,
      sha: m.sha,
      motif: m.sha === null ? m.reason : null,
      etat,
      attendu: etat === 'differs' ? (c?.edition ?? null) : null,
    }
  })]
}

const REMEDE_CARTES = '`forgemaster toolchain install` les repose (idempotent, hors-ligne)'

export function edition(v: Version): VerdictEdition {
  const base = { pieces: pieces(v), remede: null as string | null }
  const mode = `Mode d'install : ${MODE_LIBELLE[v.install.mode]}`

  // Aucune édition lue sur ce disque. Deux situations très différentes derrière la même absence, et les
  // confondre ferait passer un défaut d'artefact pour un choix de développeur.
  if (v.edition.edition_dir === null) {
    if (v.install.mode === 'checkout')
      return { ...base, etat: 'sans-edition', ton: 'info', etiquette: 'sans édition',
               titre: 'Cette instance ne vient pas d\'une édition — rien à quoi ses cartes se conformeraient',
               detail: `${mode} : les cartes viennent des siblings, pas d'un wheel` }
    return { ...base, etat: 'non-verifiee', ton: 'warn',
             etiquette: 'conformité non vérifiée',
             titre: 'Conformité des cartes NON vérifiée — ce n\'est pas « conforme »',
             detail: v.edition.reason ?? mode }
  }

  if (v.edition.state === 'differs') {
    const drift = v.edition.maps.filter((m) => m.state === 'differs').map((m) => m.name)
    return { ...base, etat: 'derive', ton: 'warn',
             etiquette: drift.length > 1 ? `${drift.length} cartes en écart` : 'carte en écart',
             titre: `${drift.join(', ')} : ce n'est pas la carte que cette édition déclare`,
             detail: `${mode} · édition lue dans ${v.edition.edition_dir}`,
             remede: REMEDE_CARTES }
  }

  // `unknown` ne se replie JAMAIS sur `conforme`. Il vaut aussi pour une édition amputée ou une carte non
  // installée — des cas où le vert serait un mensonge exact, pas une approximation.
  if (v.edition.state === 'unknown') {
    // « Au moins une » serait FAUX quand il n'y en a aucune : l'outillage illisible rend une liste vide,
    // et annoncer une comparaison partielle sur zéro carte affichée ferait chercher laquelle.
    const aucune = v.edition.maps.length === 0
    return { ...base, etat: 'non-verifiee', ton: 'info',
             etiquette: aucune ? 'aucune carte lue' : 'conformité partielle',
             titre: aucune
               ? 'Aucune carte n\'a pu être lue — ce n\'est pas « conforme »'
               : 'Au moins une carte n\'a pas pu être comparée — ce n\'est pas « conforme »',
             detail: v.edition.reason ?? `${mode} · édition lue dans ${v.edition.edition_dir}` }
  }

  return { ...base, etat: 'conforme', ton: 'ok',
           etiquette: 'cartes conformes',
           titre: `Les ${v.maps.length} cartes servies sont celles de cette édition`,
           detail: `${mode} · édition lue dans ${v.edition.edition_dir}` }
}

/** Le serveur de corpus, en UNE phrase. Troisième volet d'identité, et il ne se fond pas dans les autres :
 *  il bouge à l'édition, pas à la réinjection. Un serveur DISTANT ne rend pas de SHA, et c'est dit — le
 *  deviner serait exactement le SHA faux qui retire le doute au lieu de le lever. */
export function corpus(v: Version): string {
  const t = v.mcp
  if (t.topology === 'co-installed')
    return `Corpus MCP : co-installé ici${t.sha ? ` (${shaCourt(t.sha)})` : ''} · ${t.endpoint}`
  if (t.topology === 'remote')
    return `Corpus MCP : serveur distant · ${t.endpoint} — son SHA se demande, il ne se lit pas d'ici`
  if (t.topology === 'none')
    return 'Corpus MCP : aucun endpoint configuré — instance sans corpus à interroger'
  return 'Corpus MCP : topologie illisible sur cet hôte'
}
