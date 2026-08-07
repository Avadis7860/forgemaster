import { Alert, Button, LoadingState } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useUpdatePlan } from '@/lib/queries'

/** **La prévisualisation EST le consentement.** Pas de modale de confirmation : le geste mutant est gardé
 *  par un `GET` idempotent qui dit ce qui se passerait, exactement comme `git/sync` garde `reconcile`. Une
 *  modale demanderait « es-tu sûr ? » sans rien montrer de plus ; ceci montre.
 *
 *  Un **409** n'est pas une panne, c'est une RÉPONSE : l'instance refuse dans son état, et son texte
 *  intégral s'affiche. Dans ce cas le bouton de pose **disparaît** — laisser une action armée sous un refus
 *  serait inviter à forcer une porte que le daemon vient de fermer. */
export function UpdatePreview({ mode, cible, onLancer, enCours, bloque = null }: {
  mode: 'apply' | 'rollback'
  cible: string                        // le chemin déposé (apply) ou '' (rollback : le daemon choisit)
  onLancer: () => void
  enCours: boolean
  /** Motif d'un blocage que la PAGE connaît déjà (un geste en vol), donc avant même de demander le plan.
   *  Le serveur refuserait de toute façon (409) : ceci ne remplace pas sa vérité, ça évite d'armer une
   *  action qu'on sait morte — et ça le dit tout de suite plutôt qu'après un aller-retour. */
  bloque?: string | null
}) {
  const plan = useUpdatePlan(mode, cible, bloque === null)
  const verbe = mode === 'apply' ? 'Mettre à jour l\'instance' : 'Revenir en arrière'

  if (bloque !== null) {
    return <Alert tone="warn" title="Un geste est déjà en cours"><p>{bloque}</p></Alert>
  }

  if (plan.isPending) return <LoadingState label="Lecture de ce qui se passerait…" />

  if (plan.isError) {
    const err = plan.error
    const refus = err instanceof ApiError && err.status === 409
    return (
      <Alert tone={refus ? 'warn' : 'danger'} title={refus ? "L'instance refuse" : 'Prévisualisation impossible'}>
        <p className="whitespace-pre-wrap">{err instanceof ApiError ? err.detail : String(err)}</p>
      </Alert>
    )
  }

  return (
    <div className="space-y-3">
      {/* Les lignes de `describe` sont celles que la CLI imprime avant d'agir. Rendues TELLES QUELLES : les
          reformuler perdrait le soin mis à les écrire là où l'on sait de quoi on parle. */}
      <pre className="max-h-72 overflow-auto rounded bg-faint/5 p-3 font-mono text-xs leading-relaxed text-muted">
        {plan.data.describe.join('\n')}
      </pre>
      <Button variant="danger" size="sm" onClick={onLancer} busy={enCours}>
        {verbe}
      </Button>
      <p className="text-xs text-faint">
        L'instance va s'arrêter et revenir. Cette page suit le geste toute seule — elle n'a rien à retenir
        pour ça, et un rechargement pendant la bascule ne perd rien.
      </p>
    </div>
  )
}
