import { useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from '@tanstack/react-router'
import { useProjects, useGitSync } from '@/lib/queries'
import { cn } from '@/lib/cn'
import { syncTone, dotClasses } from '@/lib/statusTone'
import { Alert, Button, Collapsible, Eyebrow, LoadingState } from '@/components/ui'
import { NewProjectForm } from '@/components/NewProjectForm'
import type { Project } from '@/lib/schemas'

// Le lien « Réglages » du rail a été retiré (2026-07-03) : doublon du header (haut-droite), il occupait de
// la place inutile. Le signal « à régler » (onboarding incomplet) a migré sur le Réglages du header (App.tsx).

const RAIL_COLLAPSE_KEY = 'cockpit.rail.collapse'

// Idiome DS de la rangée nav (ghost) : au repos discret, hover = fond token, actif = fond surélevé. Aucune
// bordure par item (règle tissu > panneau) — le relief porte l'état, pas le panneau. Cf. RepoExplorer.
const rowClass = (active: boolean) =>
  cn(
    'block rounded-card px-2 py-1.5 transition-colors',
    active ? 'bg-surface-raised text-fg' : 'text-muted hover:bg-surface-raised hover:text-fg',
  )

/** État replié/déplié des 3 catégories du rail, PERSISTÉ en localStorage (ouvert par défaut). Une seule clé
 *  porte un objet `{ projet, outils, corpus }` → le choix de l'utilisateur tient d'une session à l'autre,
 *  sans dépendre d'un projet. Dégrade silencieusement (SSR/quota/JSON cassé) vers « tout ouvert ». */
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

/** Une entité du rail (projet ou outil) : une **rangée** sélectionnable → workspace (pas une carte). */
function EntityRow({ p, active }: { p: Project; active: string | undefined }) {
  const isActive = p.slug === active
  // Dot rollup de sync miroir : lecture SEULE du cache (même queryKey que GitPanel, enabled:false) — ne
  // déclenche AUCUN fetch réseau. Absent tant que le projet n'a pas été vérifié ; `no_mirror` = pas de dot.
  const sync = useGitSync(p.slug)
  const syncState = sync.data?.state
  return (
    <Link to="/$project" params={{ project: p.slug }} className={rowClass(isActive)}>
      <span className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          {syncState && syncState !== 'no_mirror' && (
            <span className={cn('size-1.5 shrink-0 rounded-pill', dotClasses(syncTone(syncState)))}
              title={`miroir : ${syncState}`} />
          )}
          <span className="truncate text-sm font-medium">{p.slug}</span>
        </span>
        <span className="shrink-0 text-xs text-faint">{p.backend}</span>
      </span>
      {/* Sous-titre = le nom SEULEMENT s'il apporte une info (≠ slug) — sinon on ré-affiche le slug pour
          rien (axe 5, anti-redondance : un organisateur unique par donnée). */}
      {p.name && p.name !== p.slug && <span className="block truncate text-xs text-faint">{p.name}</span>}
    </Link>
  )
}

/** Corps d'une catégorie-liste (Projets / Outils) : les rangées d'entités, ou un indice discret si vide —
 *  la taxonomie reste lisible même vide. */
function EntityList(
  { items, active, emptyHint }: { items: Project[]; active: string | undefined; emptyHint: string },
) {
  if (items.length === 0) return <p className="px-2 py-1 text-xs text-faint">{emptyHint}</p>
  return (
    <ul className="space-y-0.5">
      {items.map((p) => <li key={p.id}><EntityRow p={p} active={active} /></li>)}
    </ul>
  )
}

/** Corps d'une catégorie-explorer (Bundles / Capital-token) : une **rangée** de navigation vers la route
 *  propre de l'explorer (ressource GLOBALE, hors projet). Surlignée quand on est déjà sur cette destination. */
function ExplorerRow(
  { to, label, hint, active }:
  { to: '/bundles' | '/capital'; label: string; hint: string; active: boolean },
) {
  return (
    <Link to={to} className={rowClass(active)}>
      <span className="block text-sm font-medium">{label}</span>
      <span className="block truncate text-xs text-faint">{hint}</span>
    </Link>
  )
}

/** Rail de gauche = l'espace de travail : 3 catégories **repliables** (état persistant, variante `section`
 *  sans cadre) — `Projets` et `Outils` (entités travaillées/génériques, classées par `kind`, sélectionnables
 *  + création en pied) puis `Corpus` (les deux explorers de ressources globales — bundles + capital-token —
 *  regroupés, promus en routes propres). Contexte global du shell ; les explorers ne dépendent pas du daemon
 *  projet et restent atteignables même daemon injoignable. */
export function ProjectRail({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const projects = useProjects()
  const active = useParams({ strict: false }).project
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const { isOpen, onOpenChange } = useRailCollapse()
  // Footer « Nouveau projet » replié par défaut (densité : c'est le plus gros bloc du rail et une action
  // occasionnelle) — un déclencheur compact `Nouveau projet ▾`, jamais force-ouvert (sinon il gonfle et
  // rogne la dernière section du rail). En first_run, le chemin primaire de création/setup est le hero
  // contextualisé de la Landing (`/`) ; ce déclencheur reste la voie rapide à un clic.
  const [newOpen, setNewOpen] = useState(false)

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

      <div className="min-h-0 flex-1 space-y-1 overflow-auto p-2">
        {/* Projets + Outils — dépendent du daemon (liste des projets). */}
        {projects.isPending ? (
          <LoadingState label="Chargement de l’espace…" />
        ) : projects.isError ? (
          <Alert tone="danger" title="Daemon injoignable">
            Vérifie que <code>cockpit serve</code> tourne.
          </Alert>
        ) : (
          <>
            <Collapsible variant="section" title="Projets" open={isOpen('projet')} onOpenChange={onOpenChange('projet')}>
              <EntityList
                items={projects.data.filter((p) => p.kind !== 'tool')}
                active={active}
                emptyHint="Aucun projet — crée le premier ci-dessous."
              />
            </Collapsible>
            <Collapsible variant="section" title="Outils" open={isOpen('outils')} onOpenChange={onOpenChange('outils')}>
              <EntityList
                items={projects.data.filter((p) => p.kind === 'tool')}
                active={active}
                emptyHint="Aucun outil pour l’instant."
              />
            </Collapsible>
          </>
        )}

        {/* Corpus — ressources GLOBALES parcourables (bundles semés + capital servi par le MCP),
            indépendantes du daemon projet. Un seul regroupement : deux explorers ne méritent pas chacun
            un en-tête de catégorie solo (place perdue) — le relief porte le groupe, pas chaque rangée. */}
        <Collapsible variant="section" title="Corpus" open={isOpen('corpus')} onOpenChange={onOpenChange('corpus')}>
          <ExplorerRow
            to="/bundles"
            label="Explorer les bundles"
            hint="ce que le cockpit sème"
            active={pathname.startsWith('/bundles')}
          />
          <ExplorerRow
            to="/capital"
            label="Explorer le capital"
            hint="doc & patrons servis par le MCP"
            active={pathname.startsWith('/capital')}
          />
        </Collapsible>
      </div>

      <div className="border-t border-border p-3">
        <Collapsible
          title="Nouveau projet"
          open={newOpen}
          onOpenChange={setNewOpen}
        >
          <NewProjectForm
            title={null}
            onCreated={(project) => navigate({ to: '/$project', params: { project: project.slug } })}
          />
        </Collapsible>
      </div>
    </aside>
  )
}
