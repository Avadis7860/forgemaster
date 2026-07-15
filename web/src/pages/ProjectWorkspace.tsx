import { Outlet, useParams } from '@tanstack/react-router'
import { ApiError } from '@/lib/api'
import { useProject } from '@/lib/queries'
import { Alert, Badge } from '@/components/ui'
import { WorkspaceTabs } from '@/components/WorkspaceTabs'

/** Layout du workspace projet (IA option A) : identité condensée + barre d'onglets + onglet actif.
 *  Home du projet = l'**Accueil** (route index, Docs fondu dedans) ; Roadmap · Travail · Ops suivent. */
export function ProjectWorkspace() {
  const { project } = useParams({ from: '/$project' })
  const { data, isError, error } = useProject(project)

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-border px-6 pt-4">
        {isError ? (
          <Alert tone="danger" title="Projet introuvable" className="mb-3">
            {error instanceof ApiError ? error.detail : String(error)}
          </Alert>
        ) : (
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-lg font-semibold text-fg">{data?.name ?? project}</h1>
            {data && <Badge tone="accent">{data.backend}</Badge>}
            {data && <code className="text-xs text-faint">{data.sot_path}</code>}
          </div>
        )}
        <WorkspaceTabs project={project} className="mt-3" />
      </div>
      {/* Zone onglet : `flex-1` borné → l'onglet Ops (Terminal-ancre) remplit ; les autres onglets, plus hauts
          que le viewport, scrollent ici (min-h-0 + overflow-auto). */}
      <div className="min-h-0 flex-1 overflow-auto">
        <Outlet />
      </div>
    </div>
  )
}
