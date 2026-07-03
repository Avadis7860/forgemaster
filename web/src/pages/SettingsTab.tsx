import { Alert, Badge, Card, EmptyState, LoadingState, RefreshButton, SectionTitle } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { CredentialForm } from '@/components/credential/CredentialForm'
import { useOnboarding } from '@/lib/queries'
import type { OnboardingRequirement, SecretStoreHealth } from '@/lib/schemas'

/** Réglages / Onboarding (instance-level, self-hosted) : racine du coffre de secrets + credential par
 *  repo. Panneau **non bloquant** (le cockpit reste utilisable) — complète le bandeau du shell. Sert la
 *  question « qu'est-ce qui manque pour être opérationnel ? » sans jamais révéler un secret. */
export function SettingsTab() {
  const { data, isLoading, isError, error, refetch, isFetching } = useOnboarding()

  if (isLoading)
    return (
      <div className="p-8">
        <LoadingState label="Lecture de la configuration…" />
      </div>
    )
  if (isError || !data) {
    return (
      <div className="space-y-3 p-8">
        <Alert tone="danger" title="Configuration indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
        <RefreshButton onClick={() => refetch()} busy={isFetching} />
      </div>
    )
  }

  const backend = data.secret_store.backend
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <SectionTitle eyebrow="self-hosted" title="Réglages & onboarding" />
        <RefreshButton onClick={() => refetch()} busy={isFetching} />
      </div>

      <StoreCard store={data.secret_store} complete={data.complete} />

      <Card className="space-y-4 p-5">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-fg">Credentials par repo</p>
          <Badge tone={data.complete ? 'ok' : 'warn'} dot>
            {data.complete ? 'complet' : 'incomplet'}
          </Badge>
        </div>
        {data.requirements.length === 0 ? (
          <EmptyState title="Aucun projet" description="Crée un projet pour lier ses tokens de miroir." />
        ) : (
          <ul className="divide-y divide-border">
            {data.requirements.map((r) => (
              <RequirementRow key={r.project} req={r} backend={backend} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

/** Carte de la racine de confiance : backend actif + racine joignable (`health`) + état global. */
function StoreCard({ store, complete }: { store: SecretStoreHealth; complete: boolean }) {
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3 p-5">
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium text-fg">Coffre de secrets — backend « {store.backend} »</p>
        <p className="break-all text-sm text-muted">{store.detail}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Badge tone={store.ready ? 'ok' : 'danger'} dot>
          {store.ready ? 'racine prête' : 'racine injoignable'}
        </Badge>
        <Badge tone={complete ? 'ok' : 'warn'} dot>
          {complete ? 'onboarding complet' : 'onboarding incomplet'}
        </Badge>
      </div>
    </Card>
  )
}

/** Une ligne d'exigence : projet + état du token (lié / requis / aucun miroir) + affordance de liaison. */
function RequirementRow({ req, backend }: { req: OnboardingRequirement; backend: string }) {
  const tone = req.satisfied ? 'ok' : 'warn'
  const state = req.linked ? 'token lié' : req.needs_credential ? 'token requis (miroir)' : 'aucun miroir'
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-fg">{req.project}</span>
          <Badge tone={tone} dot>
            {state}
          </Badge>
        </div>
        {req.mirror_remote && (
          <code className="block truncate font-mono text-xs text-faint">{req.mirror_remote}</code>
        )}
      </div>
      {(req.needs_credential || req.linked) && (
        <CredentialForm project={req.project} backend={backend} linked={req.linked} compact />
      )}
    </li>
  )
}
