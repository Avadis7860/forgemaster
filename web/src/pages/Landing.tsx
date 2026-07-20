import { Link } from '@tanstack/react-router'
import { Button, Card } from '@/components/ui'
import { useOnboarding } from '@/lib/queries'

/** Accueil (aucun projet sélectionné). MINIMAL par choix (2026-07-21) : le rail gauche EST la navigation
 *  (projets · outils · bundles · capital-token). En *first-run* seulement, une carte de bienvenue guide vers
 *  le wizard `/setup` ; sinon l'accueil s'efface — les explorers de ressources globales ont leurs routes
 *  propres (`/bundles`, `/capital`), atteintes depuis le rail, plus empilées ici. */
export function Landing() {
  const { data } = useOnboarding()

  if (data?.first_run) {
    return (
      <div className="mx-auto max-w-5xl p-8">
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
      </div>
    )
  }

  // Accueil au repos : canvas discret, sans invite redondante (le rail EST la navigation). Une identité
  // sobre marque l'espace vide sans réintroduire le « Sélectionne un projet » retiré.
  return (
    <div className="flex h-full items-center justify-center p-8">
      <p className="text-sm font-medium tracking-tight text-faint">cockpit</p>
    </div>
  )
}
