import { BundleExplorer } from '@/components/bundles/BundleExplorer'

/** Destination propre de l'explorer de bundles — atteinte depuis la catégorie `bundle` du rail gauche.
 *  Ressource GLOBALE (pas l'état d'un projet) ; l'explorer est piloté par l'URL (`?bundle=&bfile=`),
 *  donc chaque vue reste deep-linkable et capturable at-rest par la boucle visuelle. */
export function Bundles() {
  return (
    <div className="mx-auto max-w-5xl space-y-8 p-8">
      <BundleExplorer />
    </div>
  )
}
