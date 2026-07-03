import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useOnboarding, useProjects } from '@/lib/queries'
import { cn } from '@/lib/cn'
import { Alert, Badge, Card, Eyebrow, EmptyState, LoadingState } from '@/components/ui'
import { NewProjectForm } from '@/components/NewProjectForm'
import type { Project } from '@/lib/schemas'

/** Entrée de navigation globale PERSISTANTE vers Réglages/onboarding (instance-level, hors projet). Vit
 *  dans le rail (là où l'œil cherche la nav) et non seulement dans le header : découvrable même quand le
 *  bandeau d'onboarding est masqué (config complète). Un point `à régler` attire l'œil si incomplet. */
function SettingsNavLink() {
  const { data } = useOnboarding()
  const incomplete = data ? !data.complete : false
  return (
    <div className="border-t border-border p-3">
      <Link
        to="/settings"
        className="flex items-center justify-between rounded-card border border-border bg-surface px-3 py-2 text-sm text-fg hover:border-border-strong"
      >
        <span className="flex items-center gap-2 font-medium">
          <span aria-hidden className="text-muted">⚙</span>
          Réglages
        </span>
        {incomplete && (
          <Badge tone="warn" dot className="whitespace-nowrap">
            à régler
          </Badge>
        )}
      </Link>
    </div>
  )
}

/** Une entité du rail (projet ou outil) : carte sélectionnable → workspace. */
function EntityCard({ p, active }: { p: Project; active: string | undefined }) {
  const isActive = p.slug === active
  return (
    <Card
      as="li"
      className={cn('transition-colors', isActive && 'border-accent-500/50 bg-surface-raised')}
    >
      <Link to="/$project" params={{ project: p.slug }} className="block rounded-card px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-fg">{p.slug}</span>
          <Badge tone={isActive ? 'accent' : 'neutral'}>{p.backend}</Badge>
        </div>
        {p.name && <p className="truncate text-xs text-muted">{p.name}</p>}
      </Link>
    </Card>
  )
}

/** Une section titrée du rail (Projets / Outils), TOUJOURS rendue — la taxonomie reste lisible même vide
 *  (indice discret `emptyHint`), pour que la structure soit visible d'un coup d'œil. */
function EntitySection(
  { label, items, active, emptyHint }:
  { label: string; items: Project[]; active: string | undefined; emptyHint: string },
) {
  return (
    <div className="space-y-1.5">
      <Eyebrow>{label}</Eyebrow>
      {items.length === 0 ? (
        <p className="px-1 text-xs text-faint">{emptyHint}</p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((p) => <EntityCard key={p.id} p={p} active={active} />)}
        </ul>
      )}
    </div>
  )
}

/** Rail de gauche = l'espace de travail : entités classées en **Projets** (travaillés) et **Outils**
 *  (génériques du framework) via `kind`, sélectionnables + création. Contexte global du shell. */
export function ProjectRail() {
  const projects = useProjects()
  const active = useParams({ strict: false }).project
  const navigate = useNavigate()

  return (
    <aside className="z-(--z-rail) flex w-72 shrink-0 flex-col border-r border-border bg-surface/40">
      <div className="border-b border-border px-4 py-3">
        <Eyebrow>Espace de travail</Eyebrow>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
        {projects.isPending ? (
          <LoadingState label="Chargement de l’espace…" />
        ) : projects.isError ? (
          <Alert tone="danger" title="Daemon injoignable">
            Vérifie que <code>cockpit serve</code> tourne.
          </Alert>
        ) : projects.data.length === 0 ? (
          <EmptyState title="Espace vide" description="Crée le premier projet ci-dessous." />
        ) : (
          <>
            <EntitySection
              label="Projets"
              items={projects.data.filter((p) => p.kind !== 'tool')}
              active={active}
              emptyHint="Aucun projet — crée le premier ci-dessous."
            />
            <EntitySection
              label="Outils"
              items={projects.data.filter((p) => p.kind === 'tool')}
              active={active}
              emptyHint="Aucun outil pour l’instant."
            />
          </>
        )}
      </div>

      <SettingsNavLink />

      <div className="border-t border-border p-3">
        <NewProjectForm onCreated={(project) => navigate({ to: '/$project', params: { project: project.slug } })} />
      </div>
    </aside>
  )
}
