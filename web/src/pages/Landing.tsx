import { EmptyState } from '@/components/ui'

/** Accueil (aucun projet sélectionné) — onboarding, jamais une page blanche. */
export function Landing() {
  return (
    <div className="mx-auto max-w-2xl p-8">
      <EmptyState
        title="Sélectionne un projet"
        description="Choisis un projet dans le rail de gauche, ou crée-en un nouveau. La forge orchestre projet → roadmap → dispatch → gate → merge."
      />
    </div>
  )
}
