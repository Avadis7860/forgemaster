import { Link } from '@tanstack/react-router'
import { cn } from '@/lib/cn'

// Onglets du workspace projet (refonte IA v3, 8 plats → 3 + accueil) : **Accueil** = index (Docs fondu dedans,
// P4) ; Dispatch+Gate → « Travail » (P2) ; Git+Runtime+Flow+Terminal → « Ops » (P3). L'onglet actif est souligné
// par l'accent via `activeProps`. `exact:true` sur l'Accueil : sinon il resterait actif sur tous les sous-onglets.
type Tab = { key: string; label: string; to: string; exact: boolean }

const TABS: Tab[] = [
  { key: 'accueil', label: 'Accueil', to: '/$project', exact: true },
  { key: 'roadmap', label: 'Roadmap', to: '/$project/roadmap', exact: false },
  { key: 'travail', label: 'Travail', to: '/$project/travail', exact: false },
  { key: 'ops', label: 'Ops', to: '/$project/ops', exact: false },
]

const BASE = 'shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors'

/** Barre d'onglets du workspace (IA option A complète). Les onglets sont navigables ; l'onglet actif est
 *  souligné par l'accent. Styles actif/inactif **mutuellement exclusifs** (`activeProps`/`inactiveProps`) —
 *  jamais empilés dans `className` : `cn` ne fait pas de tailwind-merge et le routeur CONCATÈNE `activeProps`,
 *  donc un `border-transparent`/`text-muted` de base écraserait l'accent actif par ordre-source CSS. */
export function WorkspaceTabs({ project, className }: { project: string; className?: string }) {
  return (
    <nav className={cn('-mb-px flex items-center gap-1 overflow-x-auto', className)}>
      {TABS.map((t) => (
        <Link
          key={t.key}
          to={t.to}
          params={{ project }}
          activeOptions={{ exact: t.exact }}
          className={BASE}
          activeProps={{ className: 'border-accent-500 text-fg' }}
          inactiveProps={{ className: 'border-transparent text-muted hover:text-fg' }}
        >
          {t.label}
        </Link>
      ))}
    </nav>
  )
}
