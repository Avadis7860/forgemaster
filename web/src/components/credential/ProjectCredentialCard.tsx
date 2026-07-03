import { Card } from '@/components/ui'
import { useOnboarding, useProject } from '@/lib/queries'
import { CredentialForm } from './CredentialForm'

/** Carte credential sur la vue projet (onglet Git — là où vit le push/mirror) : état du token de push +
 *  affordance lier/délier. Rend l'onboarding « token par repo » atteignable sans quitter le projet. */
export function ProjectCredentialCard({ project }: { project: string }) {
  const { data: p } = useProject(project)
  const { data: onb } = useOnboarding()
  if (!p) return null
  const backend = onb?.secret_store.backend ?? 'file'
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3 p-5">
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium text-fg">Token de push (miroir)</p>
        <p className="text-sm text-muted">
          {p.mirror_remote
            ? 'Le writeback pousse le miroir avec ce token — la DB ne garde qu’une référence, jamais le secret.'
            : 'Aucun miroir configuré : le SoT local suffit, aucun token requis.'}
        </p>
      </div>
      <CredentialForm
        project={project}
        backend={backend}
        linked={Boolean(p.credential_ref)}
        linkedRef={p.credential_ref}
      />
    </Card>
  )
}
