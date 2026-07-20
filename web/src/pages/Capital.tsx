import { CapitalExplorer } from '@/components/capital/CapitalExplorer'

/** Destination propre de l'explorer du capital-token servi par le MCP — atteinte depuis la catégorie
 *  `capital-token` du rail gauche. Ressource GLOBALE ; piloté par l'URL (`?cap=&capcol=&capref=`), deep-
 *  linkable et capturable at-rest. Dégradation honnête portée par l'explorer lui-même (statut `wired`). */
export function Capital() {
  return (
    <div className="mx-auto max-w-5xl space-y-8 p-8">
      <CapitalExplorer />
    </div>
  )
}
