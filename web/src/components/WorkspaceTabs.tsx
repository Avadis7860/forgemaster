import { Link } from '@tanstack/react-router'
import { cn } from '@/lib/cn'

// Onglets du workspace projet (IA option A). Roadmap est livré (V2) ; les autres arrivent dans leurs
// vagues (Dispatch V3, Gate V4, Terminal V5) — rendus désactivés, jamais masqués (la structure se voit).
const TABS: { key: string; label: string; enabled: boolean }[] = [
  { key: 'roadmap', label: 'Roadmap', enabled: true },
  { key: 'dispatch', label: 'Dispatch', enabled: false },
  { key: 'gate', label: 'Gate', enabled: false },
  { key: 'terminal', label: 'Terminal', enabled: false },
]

/** Barre d'onglets du workspace. V2 : seul Roadmap est navigable (route index de /$project). */
export function WorkspaceTabs({ project, className }: { project: string; className?: string }) {
  return (
    <nav className={cn('-mb-px flex items-center gap-1', className)}>
      {TABS.map((t) =>
        t.enabled ? (
          <Link
            key={t.key}
            to="/$project"
            params={{ project }}
            className="border-b-2 border-accent-500 px-3 py-2 text-sm font-medium text-fg"
          >
            {t.label}
          </Link>
        ) : (
          <span
            key={t.key}
            title="à venir"
            className="cursor-not-allowed border-b-2 border-transparent px-3 py-2 text-sm text-faint"
          >
            {t.label}
          </span>
        ),
      )}
    </nav>
  )
}
