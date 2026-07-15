import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useProjects, useGitSync } from '@/lib/queries'
import { cn } from '@/lib/cn'
import { syncTone, dotClasses } from '@/lib/statusTone'
import { Alert, Badge, Button, Card, Eyebrow, EmptyState, LoadingState } from '@/components/ui'
import { NewProjectForm } from '@/components/NewProjectForm'
import type { Project } from '@/lib/schemas'

// Le lien « Réglages » du rail a été retiré (2026-07-03) : doublon du header (haut-droite), il occupait de
// la place inutile. Le signal « à régler » (onboarding incomplet) a migré sur le Réglages du header (App.tsx).

/** Une entité du rail (projet ou outil) : carte sélectionnable → workspace. */
function EntityCard({ p, active }: { p: Project; active: string | undefined }) {
  const isActive = p.slug === active
  // Dot rollup de sync miroir : lecture SEULE du cache (même queryKey que GitPanel, enabled:false) — ne
  // déclenche AUCUN fetch réseau. Absent tant que le projet n'a pas été vérifié ; `no_mirror` = pas de dot.
  const sync = useGitSync(p.slug)
  const syncState = sync.data?.state
  return (
    <Card
      as="li"
      className={cn('transition-colors', isActive && 'border-accent-500/50 bg-surface-raised')}
    >
      <Link to="/$project" params={{ project: p.slug }} className="block rounded-card px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5">
            {syncState && syncState !== 'no_mirror' && (
              <span className={cn('size-1.5 shrink-0 rounded-pill', dotClasses(syncTone(syncState)))}
                title={`miroir : ${syncState}`} />
            )}
            <span className="truncate text-sm font-medium text-fg">{p.slug}</span>
          </span>
          <Badge tone={isActive ? 'accent' : 'neutral'}>{p.backend}</Badge>
        </div>
        {/* Sous-titre = le nom SEULEMENT s'il apporte une info (≠ slug) — sinon on ré-affiche le slug pour
            rien (axe 5, anti-redondance : un organisateur unique par donnée). */}
        {p.name && p.name !== p.slug && <p className="truncate text-xs text-muted">{p.name}</p>}
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
export function ProjectRail({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const projects = useProjects()
  const active = useParams({ strict: false }).project
  const navigate = useNavigate()

  return (
    <aside
      className={cn(
        'flex w-72 shrink-0 flex-col border-r border-border',
        // Mobile : tiroir off-canvas plein-hauteur (opaque, au-dessus du scrim) — glisse selon `open`.
        'fixed inset-y-0 left-0 z-(--z-drawer) bg-surface transition-transform duration-200',
        open ? 'translate-x-0' : '-translate-x-full',
        // Desktop (≥ md) : rail statique dans le flux, translucide, jamais masqué.
        'md:static md:z-(--z-rail) md:translate-x-0 md:bg-surface/40',
      )}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <Eyebrow>Espace de travail</Eyebrow>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Fermer" className="md:hidden">
          <span aria-hidden>✕</span>
        </Button>
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

      <div className="border-t border-border p-3">
        <NewProjectForm onCreated={(project) => navigate({ to: '/$project', params: { project: project.slug } })} />
      </div>
    </aside>
  )
}
