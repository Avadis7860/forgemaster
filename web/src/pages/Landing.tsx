import { Link } from '@tanstack/react-router'
import { Button, Card, EmptyState } from '@/components/ui'
import { BundleExplorer } from '@/components/bundles/BundleExplorer'
import { useOnboarding } from '@/lib/queries'

/** Accueil (aucun projet sélectionné). En tête, selon l'état de l'instance : carte de bienvenue *first-run*
 *  (→ wizard `/setup`) ou l'invite classique à choisir un projet. Puis, TOUJOURS, l'explorer des bundles —
 *  une ressource **globale** (offerte à la création, partagée par tous), pas l'état d'un projet : on peut
 *  auditer ce que le cockpit embarque depuis l'accueil, sans ouvrir un projet ni un repo à la main. */
export function Landing() {
  const { data } = useOnboarding()

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-8">
      {data?.first_run ? (
        <Card className="space-y-4 p-6">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-fg">Bienvenue dans ton cockpit</h2>
            <p className="text-sm text-muted">
              Ton instance est neuve. Configure-la en quelques étapes — coffre de secrets, premier projet,
              miroir GitHub optionnel — puis lance la forge : projet → roadmap → travail (dispatch → validation → merge).
            </p>
          </div>
          <Link to="/setup">
            <Button variant="primary">Démarrer la configuration</Button>
          </Link>
        </Card>
      ) : (
        <EmptyState
          title="Sélectionne un projet"
          description="Choisis un projet dans le rail de gauche, ou crée-en un nouveau. La forge orchestre projet → roadmap → travail (dispatch → validation → merge)."
        />
      )}

      <BundleExplorer />
    </div>
  )
}
