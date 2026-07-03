import { Card } from '@/components/ui'
import { useOnboarding, useProject } from '@/lib/queries'
import { CredentialForm } from './CredentialForm'
import { MirrorForm } from './MirrorForm'

/** Carte GitHub-backed sur la vue projet (onglet Git — là où vit le push/mirror) : config du **miroir**
 *  (rend GitHub-backed) puis, une fois posé, le **token de push**. Rend le flux atteignable sans quitter le
 *  projet ; cohérent avec le panneau Réglages. */
export function ProjectCredentialCard({ project }: { project: string }) {
  const { data: p } = useProject(project)
  const { data: onb } = useOnboarding()
  if (!p) return null
  const backend = onb?.secret_store.backend ?? 'file'
  return (
    <Card className="space-y-4 p-5">
      <p className="text-sm font-medium text-fg">Miroir GitHub &amp; token de push</p>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="shrink-0 text-xs uppercase tracking-wide text-faint">Miroir</span>
        <MirrorForm project={project} mirror={p.mirror_remote} />
      </div>
      {p.mirror_remote && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="shrink-0 text-xs uppercase tracking-wide text-faint">Token</span>
          <CredentialForm
            project={project}
            backend={backend}
            linked={Boolean(p.credential_ref)}
            linkedRef={p.credential_ref}
          />
        </div>
      )}
      <p className="text-xs text-faint">
        {p.mirror_remote
          ? 'Le writeback pousse le miroir avec ce token — la DB ne garde qu’une référence, jamais le secret.'
          : 'Sans miroir, le SoT local suffit ; configure un miroir pour pousser vers GitHub.'}
      </p>
    </Card>
  )
}
