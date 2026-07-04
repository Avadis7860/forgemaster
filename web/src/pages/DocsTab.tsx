import { Suspense, lazy } from 'react'
import { useParams } from '@tanstack/react-router'
import { Alert, EmptyState, LoadingState } from '@/components/ui'
import { useDocs } from '@/lib/queries'

// react-markdown est lourd → code-split : le renderer (et ses deps) ne se charge QUE quand on ouvre Docs.
const DocView = lazy(() => import('@/components/docs/DocView').then((m) => ({ default: m.DocView })))

/** Onglet Docs : la CARTE de l'outil/projet — « ce que c'est · pourquoi l'utiliser avec Claude » — lue
 *  depuis SON repo (`docs/tool-card.md`, repli `README.md`) via `GET /api/projects/{p}/docs`. La doc vit
 *  dans le repo (SoT-and-derive) : l'éditer là-bas met à jour ce que le cockpit affiche. */
export function DocsTab() {
  const project = useParams({ strict: false }).project ?? ''
  const docs = useDocs(project)

  if (!project) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState title="Aucun projet" description="Ouvre un projet pour voir sa page docs." />
      </div>
    )
  }
  if (docs.isLoading) return <div className="p-8"><LoadingState label="Chargement de la doc…" /></div>
  if (docs.isError) {
    return <div className="p-8"><Alert tone="danger" title="Docs indisponibles">{docs.error.message}</Alert></div>
  }
  if (!docs.data?.found) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          title="Pas encore de page docs"
          description="Ce dépôt n'a pas de `docs/tool-card.md` (ni de `README.md`). Ajoute-la dans le repo — le cockpit la relira."
        />
      </div>
    )
  }
  return (
    <div className="space-y-3 p-6">
      {docs.data.truncated && (
        <Alert tone="info" title="Doc tronquée">Le fichier dépasse la taille d'affichage — contenu coupé.</Alert>
      )}
      <Suspense fallback={<LoadingState label="Rendu de la doc…" />}>
        <DocView content={docs.data.content} />
      </Suspense>
    </div>
  )
}
