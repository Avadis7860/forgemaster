import { TemplateExplorer } from '@/components/templates/TemplateExplorer'

/** Destination propre de la **vitrine des templates de référence UI** — atteinte depuis la catégorie `Corpus`
 *  du rail gauche (frère de `/bundles` et `/capital`). Ressource GLOBALE ; pilotée par l'URL (`?tpl=<slug>`),
 *  deep-linkable et capturable at-rest. Dégradation honnête portée par l'explorer lui-même. */
export function Templates() {
  return (
    <div className="mx-auto max-w-5xl space-y-8 p-8">
      <TemplateExplorer />
    </div>
  )
}
