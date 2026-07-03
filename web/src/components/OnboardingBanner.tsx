import { useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui'
import { useOnboarding } from '@/lib/queries'

/** Bandeau **non bloquant** : rappelle une config d'onboarding incomplète (racine du coffre injoignable ou
 *  tokens de miroir manquants) et renvoie vers Réglages. Silencieux si tout est complet — ou en erreur/
 *  chargement (le shell reste utilisable, on ne bloque jamais sur l'onboarding). */
export function OnboardingBanner() {
  const { data } = useOnboarding()
  const navigate = useNavigate()
  if (!data || data.complete) return null

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
