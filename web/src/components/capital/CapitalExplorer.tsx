import { lazy, Suspense, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import {
  Alert, Badge, Button, Card, EmptyState, LoadingState, SectionTitle, Select,
} from '@/components/ui'
import { ApiError } from '@/lib/api'
import {
  useCapitalBody, useCapitalCollections, useCapitalSections, useCapitalStatus, useCapitalTypes,
} from '@/lib/queries'
import type { CapitalSection, CapitalType } from '@/lib/schemas'
import { DocReaderOverlay } from '@/components/docs/DocReader'

// DocView (react-markdown + remark-gfm) en chunk séparé — le corps servi (blueprint `body` / tech `content`)
// est du Markdown ; on le rend comme une vraie page de doc. Lazy → hors du bundle initial de l'accueil.
const DocView = lazy(() => import('@/components/docs/DocView').then((m) => ({ default: m.DocView })))

type Search = { cap?: string; capcol?: string; capref?: string }

/** Explorer READ-ONLY du **capital-token servi par le MCP** (l'autre moitié de « juger l'efficacité avant
 *  distribution » : pendant que BundleExplorer montre ce que le cockpit SÈME, celui-ci montre ce qu'il
 *  LOUE/POSSÈDE — `tech`/`blueprint`/`templates`). Navigation `type → collection → section → corps`, pilotée
 *  par l'URL (`?cap=&capcol=&capref=`) → deep-linkable, capturable at-rest par la boucle visuelle.
 *
 *  Dégradation honnête (le cœur) : `/status` est la porte SANS réseau — `wired:false` → on rend « non câblé »
 *  et on ne tente AUCUN parcours (pas de 503 martelé). MCP câblé mais un silo/collection cassé côté serveur →
 *  Alert honnête localisée, jamais un corps inventé. */
export function CapitalExplorer() {
  const status = useCapitalStatus()
  const search = useSearch({ strict: false }) as Search
  const navigate = useNavigate()

  const wired = status.data?.wired === true
  const types = useCapitalTypes(wired)

  const setType = (t: string) =>
    navigate({ to: '/capital', search: (p) => ({ ...p, cap: t, capcol: undefined, capref: undefined }) })
  const setScope = (s: string | undefined) =>
    navigate({ to: '/capital', search: (p) => ({ ...p, capcol: s, capref: undefined }) })
  const setRef = (r: string | undefined) =>
    navigate({ to: '/capital', search: (p) => ({ ...p, capref: r }) })

  if (status.isLoading) {
    return <CapitalSectionCard><LoadingState label="Interrogation du câblage MCP…" /></CapitalSectionCard>
  }
  if (!wired) {
    // État honnête : le capital servi n'est pas atteignable depuis cette install (pas de secret câblé). On
    // NE prétend PAS le contraire — on montre comment le câbler. Le reste du parcours reste inerte (enabled).
    return (
      <CapitalSectionCard>
        <EmptyState
          title="Capital-token non câblé"
          description="Aucun secret MCP n'est configuré sur cette install : le corpus servi (tech / blueprint / templates) n'est pas atteignable. Câble-le via l'onboarding, ou en CLI : cockpit mcp wire --secret-ref <uuid>."
        />
      </CapitalSectionCard>
    )
  }
  if (types.isLoading) {
    return <CapitalSectionCard><LoadingState label="Lecture des types servis…" /></CapitalSectionCard>
  }
  if (types.isError || !types.data) {
    return (
      <CapitalSectionCard>
        <Alert tone="danger" title="Capital indisponible">
          {types.error instanceof ApiError ? types.error.detail : String(types.error)}
        </Alert>
      </CapitalSectionCard>
    )
  }
  const list = types.data.types
  if (list.length === 0) {
    return (
      <CapitalSectionCard>
        <EmptyState title="Aucun type" description="Le MCP ne sert aucun type de corpus." />
      </CapitalSectionCard>
    )
  }

  const activeType = (search.cap && list.find((t) => t.id === search.cap)) || list[0]
  const scope = search.capcol ?? null
  const ref = search.capref ?? null

  return (
    <CapitalSectionCard>
      {/* Sélecteur de type — chips (primitive Button, toutes `secondary` → contour d'affordance, l'active
          porte une bordure accent). Calque exact du sélecteur de BundleExplorer. */}
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Type de capital">
        {list.map((t) => {
          const active = t.id === activeType.id
          return (
            <Button
              key={t.id}
              variant="secondary"
              size="sm"
              onClick={() => setType(t.id)}
              aria-pressed={active}
              className={active ? 'border-accent-500 text-fg' : 'text-muted'}
            >
              {t.id}
            </Button>
          )
        })}
      </div>

      <TypeCaption type={activeType} />

      {/* `min-w-0` sur CHAQUE enfant de grille : une grille CSS ne rétrécit pas ses items sous leur contenu
          sans ça → sinon la TOC pousse ses titres sous le corps (troncation morte) et le corps Markdown ne
          reflow pas à 390 (H1/code clippés au bord). Le track `minmax(0,…)` borne la colonne, pas l'item. */}
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
        <CapitalNavPane type={activeType} scope={scope} selectedRef={ref} onScope={setScope} onOpen={setRef} />
        {/* `self-start` + `sticky` : le corps garde sa hauteur (le message nu reste visible, pas centré dans
            un panneau étiré) et suit le scroll d'une TOC longue. */}
        <div className="min-w-0 md:sticky md:top-4 md:self-start">
          <BodyPane type={activeType.id} refToRead={ref} />
        </div>
      </div>
    </CapitalSectionCard>
  )
}

/** Le cadre commun de la section (carte + en-tête), factorisé pour que chaque état — chargement, non-câblé,
 *  erreur, contenu — porte le même chapeau (l'explorer reste identifiable même vide/en erreur). */
function CapitalSectionCard({ children }: { children: React.ReactNode }) {
  return (
    <Card className="space-y-4 p-5">
      <SectionTitle
        eyebrow="Capital-token servi"
        title="Explorer le capital-token"
        actions={<Badge tone="neutral" className="whitespace-nowrap">lecture seule</Badge>}
      />
      <p className="-mt-2 text-sm text-muted">
        Parcours le corpus servi par le MCP — la doc tierce louée (tech) et les patrons possédés (blueprint,
        templates). Pour juger le capital sur lequel le cockpit ancre ses sessions avant de le distribuer.
      </p>
      {children}
    </Card>
  )
}

/** Une ligne de contexte : la stratégie du type actif (layout · requête · lignée de distillation). Sèche,
 *  atténuée — c'est un sous-titre, pas un panneau (tissu > panneau). */
function TypeCaption({ type }: { type: CapitalType }) {
  const bits = [
    type.layout && `layout ${type.layout}`,
    type.query && `requête ${type.query}`,
    type.distilled_of && `distillé de ${type.distilled_of}`,
  ].filter(Boolean)
  return (
    <p className="-mt-1 text-sm text-muted">
      Corpus <span className="font-medium text-fg">{type.id}</span>
      {bits.length > 0 && <span className="text-faint"> · {bits.join(' · ')}</span>}
    </p>
  )
}

/** Le volet gauche : pour un silo (`layout=silo`, ex. tech/templates), un `Select` de collection (scope) PUIS
 *  la TOC de ce scope ; pour un corpus plat (blueprint), directement la TOC (sans scope). Un type silo tant
 *  qu'aucune collection choisie invite à en choisir une (pas de requête sections tirée à blanc). */
function CapitalNavPane(props: {
  type: CapitalType
  scope: string | null
  selectedRef: string | null
  onScope: (s: string | undefined) => void
  onOpen: (r: string) => void
}) {
  const { type, scope, selectedRef, onScope, onOpen } = props
  const isSilo = type.layout === 'silo'
  const collections = useCapitalCollections(type.id, isSilo)
  // Sections : pour un silo il faut un scope choisi ; pour un plat, on liste sans scope.
  const sectionsEnabled = !isSilo || !!scope
  const sections = useCapitalSections(type.id, isSilo ? scope : null, sectionsEnabled)

  return (
    <div className="min-w-0 space-y-3">
      {isSilo && (
        <CollectionPicker query={collections} scope={scope} onScope={onScope} />
      )}

      {isSilo && !scope ? (
        <p className="px-1 text-sm text-muted">Choisis une collection pour lire sa table des matières.</p>
      ) : sections.isLoading ? (
        <LoadingState label="Lecture de la table des matières…" />
      ) : sections.isError || !sections.data ? (
        <Alert tone="danger" title="Table des matières indisponible">
          {sections.error instanceof ApiError ? sections.error.detail : String(sections.error)}
        </Alert>
      ) : sections.data.sections.length === 0 ? (
        <EmptyState title="Aucune section" description="Ce scope ne sert aucune section." />
      ) : (
        <ul className="space-y-0.5">
          {sections.data.sections.map((s, i) => {
            const r = sectionRef(s, isSilo ? scope : null)
            return (
              <li key={r || i}>
                <SectionRow section={s} typeId={type.id} active={selectedRef === r} onClick={() => onOpen(r)} />
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** Le `Select` de collection d'un silo (tech a ~90 collections → un menu natif, filtrable au clavier, plutôt
 *  qu'une nuée de chips). Chaque option annote la complétude de sa source. */
function CollectionPicker(props: {
  query: ReturnType<typeof useCapitalCollections>
  scope: string | null
  onScope: (s: string | undefined) => void
}) {
  const { query, scope, onScope } = props
  if (query.isLoading) return <LoadingState label="Lecture des collections…" />
  if (query.isError || !query.data) {
    return (
      <Alert tone="danger" title="Collections indisponibles">
        {query.error instanceof ApiError ? query.error.detail : String(query.error)}
      </Alert>
    )
  }
  const cols = query.data.collections
  if (cols.length === 0) {
    return <EmptyState title="Aucune collection" description="Ce type ne sert aucune collection." />
  }
  return (
    <Select
      aria-label="Collection"
      value={scope ?? ''}
      onChange={(e) => onScope(e.target.value || undefined)}
    >
      <option value="">— choisir une collection ({cols.length}) —</option>
      {cols.map((c) => (
        <option key={c.name} value={c.name}>
          {c.name}
          {c.completeness ? ` · ${c.completeness}` : ''}
          {typeof c.pages_count === 'number' ? ` · ${c.pages_count}p` : ''}
        </option>
      ))}
    </Select>
  )
}

/** Une ligne de section : titre (mis en avant) + sous-titre atténué (statut/tags pour un blueprint, chapeau
 *  pour une page tech). Toujours la primitive Button (jamais un bouton brut, R1). */
function SectionRow({ section, typeId, active, onClick }: {
  section: CapitalSection; typeId: string; active: boolean; onClick: () => void
}) {
  const label = sectionLabel(section, typeId)
  const sub = sectionSubtitle(section, typeId)
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onClick}
      // Row active : fond surélevé + barre d'accent à gauche (relief par token, pas un panneau) → on relie
      // d'un coup d'œil le corps de droite à sa row d'origine dans une TOC longue (orientation, axe 6).
      className={active ? 'h-auto w-full justify-start border-l-2 border-accent-500 bg-surface-raised py-1.5 text-fg'
        : 'h-auto w-full justify-start border-l-2 border-transparent py-1.5'}
      aria-current={active ? 'true' : undefined}
      title={label}
    >
      <span className="shrink-0 text-xs" aria-hidden>📄</span>
      <span className="flex min-w-0 flex-1 flex-col items-start text-left">
        <span className="w-full truncate text-xs font-medium text-fg">{label}</span>
        {sub && <span className="w-full truncate text-xs text-faint">{sub}</span>}
      </span>
    </Button>
  )
}

/** Le corps de la section : Markdown rendu en page de doc (DocView). Vide → invite à choisir ; erreur (503
 *  MCP down, ou ref cassée) → Alert honnête. Le champ prose diffère par type (`body` blueprint / `content`
 *  tech) → on prend le premier présent, sinon un pretty-JSON de secours (jamais rien d'inventé). */
function BodyPane({ type, refToRead }: { type: string; refToRead: string | null }) {
  const { data, isLoading, isError, error } = useCapitalBody(type, refToRead ?? '', !!refToRead)
  const [readerOpen, setReaderOpen] = useState(false)

  if (!refToRead) {
    return (
      <Card className="flex min-h-48 flex-col items-center justify-center gap-1 p-8 text-center">
        <p className="text-sm font-medium text-fg">Aucune section sélectionnée</p>
        <p className="max-w-sm text-sm text-muted">Choisis une section dans la table des matières pour lire son corps.</p>
      </Card>
    )
  }
  if (isLoading) return <Card className="p-5"><LoadingState label="Lecture du corps…" /></Card>
  if (isError || !data) {
    return (
      <Card className="p-5">
        <Alert tone="danger" title="Corps indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      </Card>
    )
  }
  const prose = data.body ?? data.content ?? JSON.stringify(data, null, 2)
  const docTitle = data.ref.split('/').pop() || data.ref
  return (
    <Card className="flex min-h-48 flex-col overflow-hidden p-0">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2">
        <code className="min-w-0 flex-1 truncate font-mono text-xs text-fg" title={data.ref}>{data.ref}</code>
        <span className="shrink-0 text-xs text-faint">{data.type}</span>
        {/* « Lire en grand » → lecteur focus large (mesure ~72ch, grande typo). L'inline reste l'aperçu.
            Bouton LABELLÉ (pas une icône nue) : c'est le geste central du chantier, il doit se voir. */}
        <Button variant="secondary" size="sm" className="shrink-0" onClick={() => setReaderOpen(true)}>
          ⤢ Lire en grand
        </Button>
      </div>
      <div className="overflow-auto p-5">
        <Suspense fallback={<LoadingState label="Rendu Markdown…" />}>
          <DocView content={prose} />
        </Suspense>
      </div>
      <DocReaderOverlay open={readerOpen} onOpenChange={setReaderOpen}
        title={docTitle} meta={`${data.ref} · ${data.type}`} content={prose} />
    </Card>
  )
}

// -- dérivations de présentation (le serveur MCP possède la donnée ; le front dérive l'affichage) --------

/** La **ref de lecture** d'une section : `<scope>/<path>` pour un silo (tech), l'`id` pour un corpus plat
 *  (blueprint). C'est exactement ce qu'attend `read(type, ref)` côté serveur. */
function sectionRef(s: CapitalSection, scope: string | null): string {
  if (s.path) return scope ? `${scope}/${s.path}` : s.path
  return s.id ?? ''
}

/** Le libellé affiché : titre si présent, sinon l'identité (id/path). Retire un préfixe `<type> — ` REDONDANT
 *  (les titres blueprint commencent tous par « Blueprint — » alors que le chip + la caption + la sous-ligne
 *  disent déjà blueprint) : le nom distinctif remonte en tête et cesse de filer sous le panneau (axes 5+6). */
function sectionLabel(s: CapitalSection, typeId: string): string {
  const raw = s.title ?? s.id ?? s.path ?? '(sans titre)'
  return raw.replace(new RegExp(`^${typeId}\\s*[—–-]\\s*`, 'i'), '')
}

/** Le sous-titre atténué : statut + tags (blueprint), sinon le chapeau (tech), sinon le chemin/id brut. On
 *  retire le tag ÉGAL au type sélectionné (redondant : l'onglet + la caption le disent déjà) et on garde les
 *  tags de scope distinctifs. */
function sectionSubtitle(s: CapitalSection, typeId: string): string | null {
  const distinctTags = s.tags?.filter((t) => t.toLowerCase() !== typeId.toLowerCase())
  if (s.status || (distinctTags && distinctTags.length > 0)) {
    const tags = distinctTags?.slice(0, 3).join(', ')
    return [s.status, tags].filter(Boolean).join(' · ') || null
  }
  if (s.lead) return s.lead
  if (s.path && s.title) return s.path
  return null
}
