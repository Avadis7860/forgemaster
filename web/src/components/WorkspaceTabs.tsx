import { Link } from '@tanstack/react-router'
import { cn } from '@/lib/cn'

// Onglets du workspace projet (IA option A). Roadmap + Dispatch + Gate sont livrés (V2/V3/V4) ; Terminal
// arrive en V5 — rendu désactivé, jamais masqué (la structure se voit).
type Tab =
  | { key: string; label: string; enabled: true; to: string; exact: boolean }
  | { key: string; label: string; enabled: false }

const TABS: Tab[] = [
  { key: 'roadmap', label: 'Roadmap', enabled: true, to: '/$project', exact: true },
  { key: 'dispatch', label: 'Dispatch', enabled: true, to: '/$project/dispatch', exact: false },
  { key: 'gate', label: 'Gate', enabled: true, to: '/$project/gate', exact: false },
  { key: 'terminal', label: 'Terminal', enabled: false },
]

const BASE = 'border-b-2 px-3 py-2 text-sm font-medium transition-colors'

/** Barre d'onglets du workspace. V4 : Roadmap (route index) + Dispatch + Gate navigables ; l'onglet actif
 *  est souligné par l'accent via `activeProps` (le routeur sait quel onglet est courant). */
export function WorkspaceTabs({ project, className }: { project: string; className?: string }) {
  return (
    <nav className={cn('-mb-px flex items-center gap-1', className)}>
      {TABS.map((t) =>
        t.enabled ? (
          <Link
            key={t.key}
            to={t.to}
            params={{ project }}
            activeOptions={{ exact: t.exact }}
            className={cn(BASE, 'border-transparent text-muted hover:text-fg')}
            activeProps={{ className: cn(BASE, 'border-accent-500 text-fg') }}
          >
            {t.label}
          </Link>
        ) : (
          <span
            key={t.key}
            title="à venir"
            className={cn(BASE, 'cursor-not-allowed border-transparent text-faint')}
          >
            {t.label}
          </span>
        ),
      )}
    </nav>
  )
}
