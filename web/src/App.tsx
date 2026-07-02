import { Link, Outlet, useParams } from '@tanstack/react-router'
import { HealthDot } from '@/components/HealthDot'
import { ProjectRail } from '@/components/ProjectRail'

/** Shell global (IA option A) : header + rail de projets + espace de travail (Outlet). */
export function AppShell() {
  const project = useParams({ strict: false }).project
  return (
    <div className="flex h-full flex-col">
      <header className="z-(--z-header) flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-4">
        <div className="flex items-baseline gap-2">
          <Link to="/" className="text-sm font-semibold tracking-tight text-fg">
            cockpit
          </Link>
          {project && (
            <span className="text-sm text-muted">
              <span className="text-faint">/</span> {project}
            </span>
          )}
        </div>
        <HealthDot />
      </header>

      <div className="flex min-h-0 flex-1">
        <ProjectRail />
        <main className="min-w-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
