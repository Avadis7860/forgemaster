import { useState } from 'react'
import { Link, Outlet, useLocation, useParams } from '@tanstack/react-router'
import { HealthDot } from '@/components/HealthDot'
import { OnboardingBanner } from '@/components/OnboardingBanner'
import { ProjectRail } from '@/components/ProjectRail'
import { Badge } from '@/components/ui'
import { useOnboarding } from '@/lib/queries'

/** Shell global (IA option A) : header + bandeau onboarding non bloquant + rail de projets + espace de
 *  travail (Outlet). Sous `md` le rail devient un tiroir off-canvas (axe 8, cockpit nomade) : le contenu
 *  reprend 100 % de la largeur, le tiroir s'ouvre par le hamburger et se ferme au scrim / à la navigation. */
export function AppShell() {
  const project = useParams({ strict: false }).project
  const { data: onb } = useOnboarding()
  const settingsIncomplete = onb ? !onb.complete : false
  const [railOpen, setRailOpen] = useState(false)
  // Fermer le tiroir dès qu'on navigue (clic sur une carte projet / création) — sur mobile uniquement,
  // sur desktop il est statique et l'état est ignoré (md:translate-x-0). Ajustement d'état PENDANT le rendu
  // au changement de route (pattern React « you might not need an effect ») : pas d'effet → pas de render
  // en cascade (règle react-hooks/set-state-in-effect).
  const { pathname } = useLocation()
  const [prevPath, setPrevPath] = useState(pathname)
  if (pathname !== prevPath) {
    setPrevPath(pathname)
    setRailOpen(false)
  }

  return (
    <div className="flex h-full flex-col">
      <header className="z-(--z-header) flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-4">
        <div className="flex min-w-0 items-center gap-2 whitespace-nowrap">
          <button
            type="button"
            onClick={() => setRailOpen(true)}
            aria-label="Ouvrir l’espace de travail"
            className="-ml-1 shrink-0 rounded-card px-2 py-1 text-muted hover:text-fg md:hidden"
          >
            <span aria-hidden>☰</span>
          </button>
          <Link to="/" className="shrink-0 text-sm font-semibold tracking-tight text-fg">
            cockpit
          </Link>
          {project && (
            <span className="truncate text-sm text-muted">
              <span className="text-faint">/</span> {project}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <Link to="/settings" className="flex items-center gap-1.5 text-sm text-muted hover:text-fg">
            <span aria-hidden>⚙</span>
            Réglages
            {settingsIncomplete && <Badge tone="warn" dot>à régler</Badge>}
          </Link>
          <HealthDot />
        </div>
      </header>

      <OnboardingBanner />

      <div className="flex min-h-0 flex-1">
        {/* Scrim mobile — au-dessus du header (z-dock), sous le tiroir (z-drawer) ; ferme au tap. */}
        {railOpen && (
          <div
            onClick={() => setRailOpen(false)}
            aria-hidden
            className="fixed inset-0 z-(--z-dock) bg-black/60 md:hidden"
          />
        )}
        <ProjectRail open={railOpen} onClose={() => setRailOpen(false)} />
        <main className="min-w-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
