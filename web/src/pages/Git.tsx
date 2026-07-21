import { GitExplorer } from '@/components/git/GitExplorer'

/** Destination propre de la **surface Git de plein droit** — atteinte depuis la catégorie `Corpus` du rail
 *  gauche (frère de `/bundles`, `/capital`, `/templates`). Git étant per-projet, l'explorer porte son propre
 *  sélecteur de projet ; piloté par l'URL (`?project=<slug>&view=<vue>&sha=<commit>`), deep-linkable et
 *  capturable at-rest. Remplace l'ex-drawer `?panel=git` d'Ops (un seul organisateur git). Conteneur plus
 *  large que la vitrine — arbre/historique/diff ont besoin d'espace. */
export function Git() {
  return (
    <div className="mx-auto max-w-6xl space-y-8 p-8">
      <GitExplorer />
    </div>
  )
}
