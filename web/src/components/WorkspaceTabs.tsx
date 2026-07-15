import { Link } from '@tanstack/react-router'
import { cn } from '@/lib/cn'

// Onglets du workspace projet (IA option A, complète) : Roadmap + Dispatch + Gate + Terminal, tous livrés
// (V2/V3/V4/V5). L'onglet actif est souligné par l'accent via `activeProps`.
type Tab = { key: string; label: string; to: string; exact: boolean }

const TABS: Tab[] = [
  { key: 'roadmap', label: 'Roadmap', to: '/$project', exact: true },
  { key: 'docs', label: 'Docs', to: '/$project/docs', exact: false },
  { key: 'dispatch', label: 'Dispatch', to: '/$project/dispatch', exact: false },
  { key: 'gate', label: 'Gate', to: '/$project/gate', exact: false },
  { key: 'git', label: 'Git', to: '/$project/git', exact: false },
  { key: 'runtime', label: 'Runtime', to: '/$project/runtime', exact: false },
  { key: 'flow', label: 'Flow', to: '/$project/flow', exact: false },
  { key: 'terminal', label: 'Terminal', to: '/$project/terminal', exact: false },
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
