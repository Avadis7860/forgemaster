import { Link } from '@tanstack/react-router'
import { cn } from '@/lib/cn'

// Onglets du workspace projet (IA option A, complète) : Roadmap + Dispatch + Gate + Terminal, tous livrés
// (V2/V3/V4/V5). L'onglet actif est souligné par l'accent via `activeProps`.
type Tab = { key: string; label: string; to: string; exact: boolean }

const TABS: Tab[] = [
  { key: 'roadmap', label: 'Roadmap', to: '/$project', exact: true },
  { key: 'dispatch', label: 'Dispatch', to: '/$project/dispatch', exact: false },
  { key: 'gate', label: 'Gate', to: '/$project/gate', exact: false },
  { key: 'git', label: 'Git', to: '/$project/git', exact: false },
  { key: 'flow', label: 'Flow', to: '/$project/flow', exact: false },
  { key: 'terminal', label: 'Terminal', to: '/$project/terminal', exact: false },
]

const BASE = 'border-b-2 px-3 py-2 text-sm font-medium transition-colors'

/** Barre d'onglets du workspace (IA option A complète). Les 4 onglets sont navigables ; l'onglet actif est
 *  souligné par l'accent via `activeProps` (le routeur sait quel onglet est courant). */
export function WorkspaceTabs({ project, className }: { project: string; className?: string }) {
  return (
    <nav className={cn('-mb-px flex items-center gap-1', className)}>
      {TABS.map((t) => (
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
      ))}
    </nav>
  )
}
