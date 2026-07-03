import { Link } from '@tanstack/react-router'
import { Alert, Badge, Button, Card, LoadingState, RefreshButton, SectionTitle } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/cn'
import { CredentialForm } from '@/components/credential/CredentialForm'
import { MirrorForm } from '@/components/credential/MirrorForm'
import { NewProjectForm } from '@/components/NewProjectForm'
import { useOnboarding } from '@/lib/queries'
import type { OnboardingStatus } from '@/lib/schemas'

/** Wizard 1er-démarrage (`/setup`) — la porte d'entrée guidée d'une instance self-hostée. **Non bloquant**
 *  (route normale, quittable) et ré-ouvrable ; il SÉQUENCE les affordances existantes (coffre, création de
 *  projet, miroir+token) en étapes à état vivant, plutôt que de laisser un nouvel arrivant devant un rail
 *  vide. Distingue *instance neuve* (first_run) de *déjà réglé*. */
export function SetupWizard() {
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

  const storeReady = data.secret_store.ready
  const hasProject = data.project_count > 0
  const unsatisfied = data.requirements.filter((r) => !r.satisfied)
  const allGood = storeReady && hasProject && unsatisfied.length === 0

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <SectionTitle eyebrow="self-hosted" title="Configuration du cockpit" />
          <p className="text-sm text-muted">
            Quelques étapes pour rendre ton instance opérationnelle. Tu peux quitter et revenir à tout moment.
          </p>
        </div>
        <RefreshButton onClick={() => refetch()} busy={isFetching} />
      </div>

      {allGood && (
        <Alert tone="ok" title="Ton cockpit est prêt">
          Tout est configuré. Cette page reste accessible depuis Réglages si tu ajoutes des projets plus tard.
        </Alert>
      )}

      <Step n={1} title="Bienvenue" done>
        <p className="text-sm text-muted">
          Tu héberges ta propre instance de cockpit — une forge locale : projet → roadmap → dispatch worker →
          gate → merge. Aucun compte serveur, aucun secret en clair. On configure l'essentiel ci-dessous.
        </p>
      </Step>

      <Step n={2} title="Coffre de secrets" done={storeReady}>
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-fg">backend « {data.secret_store.backend} »</span>
            <Badge tone={storeReady ? 'ok' : 'danger'} dot>
              {storeReady ? 'racine prête' : 'racine injoignable'}
            </Badge>
          </div>
          <p className="break-all text-sm text-muted">{data.secret_store.detail}</p>
          {data.secret_store.backend === 'file' && (
            <p className="text-xs text-faint">
              Défaut zéro-config : la clé de chiffrement est créée à la 1ʳᵉ écriture. Pour un coffre Bitwarden,
              pose <code>COCKPIT_SECRET_STORE=bws</code> + <code>BWS_ACCESS_TOKEN</code> et redémarre le daemon.
            </p>
          )}
        </div>
      </Step>

      <Step n={3} title="Ton premier projet" done={hasProject}>
        {hasProject ? (
          <p className="text-sm text-muted">
            {data.project_count} projet{data.project_count > 1 ? 's' : ''} créé
            {data.project_count > 1 ? 's' : ''}. Retrouve-les dans le rail de gauche.
          </p>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted">
              Crée ton premier projet. Le miroir GitHub est optionnel — tu pourras l'ajouter plus tard.
            </p>
            <NewProjectForm title={null} submitLabel="Créer ce projet" />
          </div>
        )}
      </Step>

      <Step n={4} title="Miroir GitHub & token (optionnel)" done={hasProject && unsatisfied.length === 0}>
        <TokenStep data={data} unsatisfied={unsatisfied} hasProject={hasProject} />
      </Step>

      <Step n={5} title="Prêt à travailler" done={allGood}>
        <div className="space-y-3 text-sm text-muted">
          <p>
            Le daemon sert l'API et l'UI (<code>cockpit serve --host 0.0.0.0</code> pour l'exposer au réseau
            local). Ouvre un projet dans le rail pour lancer roadmap, dispatch et gate.
          </p>
          <div className="flex flex-wrap gap-2">
            <Link to="/">
              <Button variant="primary">Aller au cockpit</Button>
            </Link>
            <Link to="/settings">
              <Button variant="secondary">Réglages détaillés</Button>
            </Link>
          </div>
        </div>
      </Step>
    </div>
  )
}

/** Étape numérotée à état vivant : pastille ✓ (fait) / numéro (à faire) + titre + corps. */
function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: React.ReactNode }) {
  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center gap-3">
        <span
          className={cn(
            'flex size-7 shrink-0 items-center justify-center rounded-full border border-border text-sm',
            done ? 'text-fg' : 'text-muted',
          )}
          aria-hidden
        >
          {done ? '✓' : n}
        </span>
        <p className="text-sm font-medium text-fg">{title}</p>
        <Badge tone={done ? 'ok' : 'neutral'} dot className="ml-auto">
          {done ? 'fait' : 'à faire'}
        </Badge>
      </div>
      <div className="pl-10">{children}</div>
    </Card>
  )
}

/** Corps de l'étape token : sans projet → invite à l'étape 3 ; sinon, pour chaque projet non satisfait,
 *  réutilise MirrorForm (config du miroir) puis CredentialForm (liaison du token). Tout satisfait → ✅. */
function TokenStep(
  { data, unsatisfied, hasProject }:
  { data: OnboardingStatus; unsatisfied: OnboardingStatus['requirements']; hasProject: boolean },
) {
  if (!hasProject)
    return <p className="text-sm text-muted">Crée d'abord un projet (étape 3) pour configurer son miroir.</p>
  if (unsatisfied.length === 0)
    return (
      <p className="text-sm text-muted">
        Rien à lier : tes projets sont locaux, ou leurs miroirs ont déjà un token. Un projet à miroir GitHub
        demande un token de push — configure-le ici ou dans Réglages.
      </p>
    )
  return (
    <ul className="space-y-4">
      {unsatisfied.map((r) => (
        <li key={r.project} className="space-y-3">
          <span className="text-sm font-medium text-fg">{r.project}</span>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="shrink-0 text-xs uppercase tracking-wide text-faint">Miroir</span>
            <MirrorForm project={r.project} mirror={r.mirror_remote} />
          </div>
          {r.needs_credential && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="shrink-0 text-xs uppercase tracking-wide text-faint">Token</span>
              <CredentialForm project={r.project} backend={data.secret_store.backend} linked={r.linked} compact />
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
