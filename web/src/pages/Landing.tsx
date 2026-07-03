import { Link } from '@tanstack/react-router'
import { Button, Card, EmptyState } from '@/components/ui'
import { useOnboarding } from '@/lib/queries'

/** Accueil (aucun projet sélectionné). Sur une **instance neuve** (first_run) → carte de bienvenue qui
 *  mène au wizard `/setup` (jamais un rail vide sans direction) ; sinon l'onboarding classique. */
export function Landing() {
  const { data } = useOnboarding()

  if (data?.first_run) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <Card className="space-y-4 p-6">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-fg">Bienvenue dans ton cockpit</h2>
            <p className="text-sm text-muted">
              Ton instance est neuve. Configure-la en quelques étapes — coffre de secrets, premier projet,
              miroir GitHub optionnel — puis lance la forge : projet → roadmap → dispatch → gate → merge.
            </p>
          </div>
          <Link to="/setup">
            <Button variant="primary">Démarrer la configuration</Button>
          </Link>
        </Card>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl p-8">
      <EmptyState
        title="Sélectionne un projet"
        description="Choisis un projet dans le rail de gauche, ou crée-en un nouveau. La forge orchestre projet → roadmap → dispatch → gate → merge."
      />
    </div>
  )
}
