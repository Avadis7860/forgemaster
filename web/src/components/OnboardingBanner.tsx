import { useLocation, useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui'
import { useOnboarding } from '@/lib/queries'

/** Bandeau **non bloquant** : sur une **instance neuve** (first_run) il invite au wizard de démarrage
 *  (`/setup`) ; sinon il rappelle une config incomplète (coffre injoignable / tokens de miroir manquants) et
 *  renvoie vers **Réglages** (`/settings`, la surface complète miroir+token par repo — le wizard ne gère plus
 *  les tokens). Silencieux quand tout est réglé — ou en erreur/chargement (le shell reste utilisable, on ne
 *  bloque jamais sur l'onboarding). */
export function OnboardingBanner() {
  const { data } = useOnboarding()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  if (!data) return null
  if (data.first_run) {
    // La Landing first_run (`/`) porte déjà l'accueil + son CTA accent contextualisé (Landing.tsx) :
    // ne pas dupliquer la bannière ici (un seul accent par vue, axe 2). Le nudge reste sur les autres routes.
    if (pathname === '/') return null
    return (
      <div className="flex items-center justify-between gap-3 border-b border-border bg-surface-raised px-4 py-2">
        <p className="text-sm text-fg">
          <span className="font-medium">Bienvenue</span> — configure ton cockpit en quelques étapes.
        </p>
        <Button size="sm" variant="primary" onClick={() => navigate({ to: '/setup' })}>
          Démarrer
        </Button>
      </div>
    )
  }
  if (data.complete) return null

  const missing = data.requirements.filter((r) => !r.satisfied).length
  const message = !data.secret_store.ready
    ? 'coffre de secrets non configuré'
    : `${missing} token${missing > 1 ? 's' : ''} de miroir requis`

  return (
    <div className="flex items-center justify-between gap-3 border-b border-warn-500/30 bg-warn-500/10 px-4 py-2">
      <p className="text-sm text-warn-500">
        <span className="font-medium">Onboarding incomplet</span> — {message}.
      </p>
      <Button size="sm" variant="secondary" onClick={() => navigate({ to: '/settings' })}>
        Régler
      </Button>
    </div>
  )
}
