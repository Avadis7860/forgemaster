import { useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from '@tanstack/react-router'
import { useProjects, useGitSync } from '@/lib/queries'
import { cn } from '@/lib/cn'
import { syncTone, dotClasses } from '@/lib/statusTone'
import { Alert, Badge, Button, Card, Collapsible, Eyebrow, LoadingState } from '@/components/ui'
import { NewProjectForm } from '@/components/NewProjectForm'
import type { Project } from '@/lib/schemas'

// Le lien « Réglages » du rail a été retiré (2026-07-03) : doublon du header (haut-droite), il occupait de
// la place inutile. Le signal « à régler » (onboarding incomplet) a migré sur le Réglages du header (App.tsx).

const RAIL_COLLAPSE_KEY = 'cockpit.rail.collapse'

/** État replié/déplié des 4 catégories du rail, PERSISTÉ en localStorage (ouvert par défaut). Une seule clé
 *  porte un objet `{ projet, outils, bundle, capital }` → le choix de l'utilisateur tient d'une session à
 *  l'autre, sans dépendre d'un projet. Dégrade silencieusement (SSR/quota/JSON cassé) vers « tout ouvert ». */
function useRailCollapse() {
  const [state, setState] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem(RAIL_COLLAPSE_KEY) ?? '{}') as Record<string, boolean>
    } catch {
      return {}
    }
  })
  const isOpen = (key: string) => state[key] ?? true
  const onOpenChange = (key: string) => (next: boolean) =>
    setState((s) => {
      const merged = { ...s, [key]: next }
      try {
        localStorage.setItem(RAIL_COLLAPSE_KEY, JSON.stringify(merged))
      } catch {
        /* stockage indisponible (mode privé / quota) — l'état reste en mémoire pour la session */
      }
      return merged
    })
  return { isOpen, onOpenChange }
}

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

/** Corps d'une catégorie-liste (Projets / Outils) : les cartes d'entités, ou un indice discret si vide —
 *  la taxonomie reste lisible même vide. */
function EntityList(
  { items, active, emptyHint }: { items: Project[]; active: string | undefined; emptyHint: string },
) {
  if (items.length === 0) return <p className="px-1 text-xs text-faint">{emptyHint}</p>
  return (
    <ul className="space-y-1.5">
      {items.map((p) => <EntityCard key={p.id} p={p} active={active} />)}
    </ul>
  )
}

/** Corps d'une catégorie-explorer (Bundles / Capital-token) : une carte de navigation vers la route propre
 *  de l'explorer (ressource GLOBALE, hors projet). Surlignée quand on est déjà sur cette destination. */
function ExplorerCard(
  { to, label, hint, active }:
  { to: '/bundles' | '/capital'; label: string; hint: string; active: boolean },
) {
  return (
    <Card className={cn('transition-colors', active && 'border-accent-500/50 bg-surface-raised')}>
      <Link to={to} className="block rounded-card px-3 py-2">
        <span className="text-sm font-medium text-fg">{label}</span>
        <p className="truncate text-xs text-muted">{hint}</p>
      </Link>
    </Card>
  )
}

/** Rail de gauche = l'espace de travail : 4 catégories **repliables** (état persistant) — `Projets` et
 *  `Outils` (entités travaillées/génériques, classées par `kind`, sélectionnables + création en pied) puis
 *  `Bundles` et `Capital-token` (navigation vers les explorers de ressources globales, promus en routes
 *  propres). Contexte global du shell ; les explorers ne dépendent pas du daemon projet et restent
 *  atteignables même daemon injoignable. */
export function ProjectRail({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const projects = useProjects()
  const active = useParams({ strict: false }).project
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const { isOpen, onOpenChange } = useRailCollapse()

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

      <div className="min-h-0 flex-1 space-y-2.5 overflow-auto p-3">
        {/* Projets + Outils — dépendent du daemon (liste des projets). */}
        {projects.isPending ? (
          <LoadingState label="Chargement de l’espace…" />
        ) : projects.isError ? (
          <Alert tone="danger" title="Daemon injoignable">
            Vérifie que <code>cockpit serve</code> tourne.
          </Alert>
        ) : (
          <>
            <Collapsible title="Projets" open={isOpen('projet')} onOpenChange={onOpenChange('projet')}>
              <EntityList
                items={projects.data.filter((p) => p.kind !== 'tool')}
                active={active}
                emptyHint="Aucun projet — crée le premier ci-dessous."
              />
            </Collapsible>
            <Collapsible title="Outils" open={isOpen('outils')} onOpenChange={onOpenChange('outils')}>
              <EntityList
                items={projects.data.filter((p) => p.kind === 'tool')}
                active={active}
                emptyHint="Aucun outil pour l’instant."
              />
            </Collapsible>
          </>
        )}

        {/* Bundles + Capital-token — ressources GLOBALES, indépendantes du daemon projet. */}
        <Collapsible title="Bundles" open={isOpen('bundle')} onOpenChange={onOpenChange('bundle')}>
          <ExplorerCard
            to="/bundles"
            label="Explorer les bundles"
            hint="ce que le cockpit sème"
            active={pathname.startsWith('/bundles')}
          />
        </Collapsible>
        <Collapsible title="Capital-token" open={isOpen('capital')} onOpenChange={onOpenChange('capital')}>
          <ExplorerCard
            to="/capital"
            label="Explorer le capital"
            hint="doc & patrons servis par le MCP"
            active={pathname.startsWith('/capital')}
          />
        </Collapsible>
      </div>

      <div className="border-t border-border p-3">
        <NewProjectForm onCreated={(project) => navigate({ to: '/$project', params: { project: project.slug } })} />
      </div>
    </aside>
  )
}
