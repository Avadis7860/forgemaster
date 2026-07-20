import { lazy, Suspense } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import {
  Alert, Badge, Button, Card, Collapsible, EmptyState, LoadingState, SectionTitle, Segmented,
} from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useBundleFile, useBundleTree, useTypes } from '@/lib/queries'
import type { BundleFile, BundleFileEntry } from '@/lib/schemas'
import type { Tone } from '@/lib/statusTone'

// DocView (react-markdown + remark-gfm) en chunk séparé — mêmes économies que DocsTab (lazy, pas dans le
// bundle initial de l'accueil). Un fichier .md du bundle est rendu comme une vraie page ; le reste en mono.
const DocView = lazy(() => import('@/components/docs/DocView').then((m) => ({ default: m.DocView })))

// Taxonomie de curation CÔTÉ FRONT : libellé + ordre + tone. Les clés (`group`) sont posées par le serveur
// (`routes/bundles._classify`) ; ici on ne fait que présenter. Ordre = ce qui porte la qualité d'abord, la
// plomberie repliée en dernier — pour « juger l'efficacité » d'un bundle sans se noyer dans l'outillage.
const GROUPS: ReadonlyArray<{ key: string; label: string; tone: Tone; defaultOpen: boolean; hint: string }> = [
  { key: 'method', label: 'Méthode & Persona', tone: 'accent', defaultOpen: true, hint: 'le « comment » — ce qui porte la qualité' },
  { key: 'deploy', label: 'Contrat de déploiement', tone: 'info', defaultOpen: true, hint: 'manifeste, roadmap de lancement, compose/Docker' },
  { key: 'seed', label: 'Seed runnable', tone: 'ok', defaultOpen: true, hint: 'la source réellement semée' },
  { key: 'docs', label: 'Docs', tone: 'purple', defaultOpen: false, hint: 'prose de haut niveau' },
  { key: 'plumbing', label: 'Plomberie', tone: 'neutral', defaultOpen: false, hint: "config d'outillage (repliée)" },
]

type View = 'curated' | 'full'
type Search = { bundle?: string; bfile?: string; bview?: View }

/** Explorer READ-ONLY de l'intérieur des bundles vendorés (grade la surface P5). Ressource GLOBALE (offerte à
 *  la création, partagée par tous les projets) → vit sur le Landing top-level, pas dans un workspace projet.
 *  Piloté par l'URL (`?bundle=&bfile=&bview=`) → chaque vue est **deep-linkable** (capturable at-rest par la
 *  boucle visuelle). Deux GET idempotents (arbre, fichier) servent tout — zéro mutation, goto-safe. */
export function BundleExplorer() {
  const { data: types, isLoading, isError, error } = useTypes()
  const search = useSearch({ strict: false }) as Search
  const navigate = useNavigate()

  if (isLoading) return <SectionCard><LoadingState label="Lecture des bundles offerts…" /></SectionCard>
  if (isError || !types) {
    return (
      <SectionCard>
        <Alert tone="danger" title="Bundles indisponibles">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      </SectionCard>
    )
  }
  if (types.length === 0) {
    return (
      <SectionCard>
        <EmptyState title="Aucun bundle" description="Aucun type de projet offert (registre vide ou tout fail-closed)." />
      </SectionCard>
    )
  }

  const activeType = search.bundle && types.some((t) => t.type === search.bundle)
    ? search.bundle
    : types[0].type
  const view: View = search.bview === 'full' ? 'full' : 'curated'
  const file = search.bfile ?? null

  const setType = (t: string) =>
    navigate({ to: '/', search: (p) => ({ ...p, bundle: t, bfile: undefined }) })
  const setView = (v: View) => navigate({ to: '/', search: (p) => ({ ...p, bview: v }) })
  const setFile = (f: string | undefined) =>
    navigate({ to: '/', search: (p) => ({ ...p, bfile: f }) })

  return (
    <SectionCard>
      {/* Sélecteur de type — chips (primitive Button, jamais un bouton brut). Le type actif est mis en avant. */}
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Type de bundle">
        {types.map((t) => (
          <Button
            key={t.type}
            variant={t.type === activeType ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setType(t.type)}
            className={t.type === activeType ? 'bg-surface-raised text-fg' : ''}
          >
            {t.type}
            <span className="ml-2 text-xs text-faint">v{t.version}</span>
          </Button>
        ))}
      </div>

      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted">
          L'intérieur servi = ce qu'un projet <span className="font-medium text-fg">{activeType}</span> reçoit au seed.
        </p>
        <Segmented<View>
          ariaLabel="Densité de l'arbre"
          value={view}
          onChange={setView}
          options={[{ value: 'curated', label: 'Curé' }, { value: 'full', label: 'Tout voir' }]}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
        <BundleTreePane type={activeType} view={view} selected={file} onOpen={setFile} />
        <BundleFilePane type={activeType} file={file} />
      </div>
    </SectionCard>
  )
}

/** Le cadre commun de la section (carte + en-tête), factorisé pour que chaque état — chargement, erreur,
 *  contenu — porte le même chapeau (l'explorer reste identifiable même vide/en erreur). */
function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <Card className="space-y-4 p-5">
      <SectionTitle
        eyebrow="Capital embarqué"
        title="Explorer les bundles"
        actions={<Badge tone="neutral">lecture seule</Badge>}
      />
      <p className="-mt-2 text-sm text-muted">
        Parcours l'intérieur de chaque bundle vendoré — l'arbre et le corps des fichiers, au-delà des
        métadonnées. Pour juger ce que le cockpit embarque avant de le distribuer.
      </p>
      {children}
    </Card>
  )
}

/** Arbre d'un bundle. Vue **curée** : fichiers groupés par ce qui porte la qualité (méthode → deploy → seed
 *  → docs → plomberie repliée). Vue **complète** : la liste plate triée de TOUS les fichiers. Les deux rendent
 *  la même donnée servie (`useBundleTree`) — le toggle ne change que la présentation. */
function BundleTreePane(props: {
  type: string
  view: View
  selected: string | null
  onOpen: (path: string) => void
}) {
  const { type, view, selected, onOpen } = props
  const { data, isLoading, isError, error } = useBundleTree(type)

  if (isLoading) return <LoadingState label="Lecture de l'arbre…" />
  if (isError || !data) {
    return (
      <Alert tone="danger" title="Arbre indisponible">
        {error instanceof ApiError ? error.detail : String(error)}
      </Alert>
    )
  }
  if (data.files.length === 0) {
    return <EmptyState title="Bundle vide" description="Aucun fichier dans le bundle composé." />
  }

  if (view === 'full') {
    return (
      <ul className="space-y-0.5">
        {data.files.map((f) => (
          <li key={f.path}>
            <FileRow entry={f} active={selected === f.path} onClick={() => onOpen(f.path)} />
          </li>
        ))}
      </ul>
    )
  }

  // Curé : un Collapsible par groupe non vide, dans l'ordre de la taxonomie (plomberie repliée par défaut).
  const byGroup: Record<string, BundleFileEntry[]> = {}
  for (const f of data.files) (byGroup[f.group] ??= []).push(f)
  return (
    <div className="space-y-2">
      {GROUPS.filter((g) => byGroup[g.key]?.length).map((g) => (
        <Collapsible
          key={g.key}
          defaultOpen={g.defaultOpen}
          title={
            <span className="flex items-center gap-2">
              <Badge tone={g.tone} dot>{g.label}</Badge>
              <span className="text-xs text-faint">{byGroup[g.key].length} · {g.hint}</span>
            </span>
          }
        >
          <ul className="space-y-0.5 pt-1">
            {byGroup[g.key].map((f) => (
              <li key={f.path}>
                <FileRow entry={f} active={selected === f.path} onClick={() => onOpen(f.path)} showGroup={false} />
              </li>
            ))}
          </ul>
        </Collapsible>
      ))}
    </div>
  )
}

/** Une ligne de fichier : chemin coupé en dossier (atténué) + nom (mis en avant). En vue plate, un point de
 *  couleur rappelle le groupe. Toujours la primitive Button (jamais un bouton brut, R1). */
function FileRow({
  entry, active, onClick, showGroup = true,
}: { entry: BundleFileEntry; active: boolean; onClick: () => void; showGroup?: boolean }) {
  const slash = entry.path.lastIndexOf('/')
  const dir = slash >= 0 ? entry.path.slice(0, slash + 1) : ''
  const base = slash >= 0 ? entry.path.slice(slash + 1) : entry.path
  const tone = GROUPS.find((g) => g.key === entry.group)?.tone ?? 'neutral'
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onClick}
      className={active ? 'w-full justify-start bg-surface-raised text-fg' : 'w-full justify-start'}
      title={entry.path}
    >
      {showGroup && <span className={`size-1.5 shrink-0 rounded-pill ${dotClass(tone)}`} aria-hidden />}
      <span className="min-w-0 truncate font-mono text-xs">
        <span className="text-faint">{dir}</span>
        <span className="text-fg">{base}</span>
      </span>
    </Button>
  )
}

/** Le corps du fichier sélectionné : un `.md` rendu en page de doc (DocView), sinon le texte en mono numéroté
 *  (calque de la visionneuse du RepoExplorer). Vide → invite à choisir ; erreur (404 fichier absent) → Alert. */
function BundleFilePane({ type, file }: { type: string; file: string | null }) {
  const { data, isLoading, isError, error } = useBundleFile(type, file ?? '')

  if (!file) {
    return (
      <Card className="flex min-h-48 items-center justify-center p-5">
        <EmptyState title="Aucun fichier" description="Choisis un fichier dans l'arbre pour l'afficher." />
      </Card>
    )
  }
  if (isLoading) return <Card className="p-5"><LoadingState label="Lecture du fichier…" /></Card>
  if (isError || !data) {
    return (
      <Card className="p-5">
        <Alert tone="danger" title="Fichier indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      </Card>
    )
  }
  return (
    <Card className="flex min-h-48 flex-col overflow-hidden p-0">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2">
        <code className="min-w-0 flex-1 truncate font-mono text-xs text-fg" title={data.path}>{data.path}</code>
        <span className="shrink-0 text-xs text-faint">{data.content.split('\n').length} lignes</span>
      </div>
      <BundleFileBody file={data} />
    </Card>
  )
}

/** Rendu du corps : Markdown (page de doc) pour un `.md`, sinon texte numéroté en mono. */
function BundleFileBody({ file }: { file: BundleFile }) {
  if (file.path.endsWith('.md')) {
    return (
      <div className="overflow-auto p-5">
        <Suspense fallback={<LoadingState label="Rendu Markdown…" />}>
          <DocView content={file.content} />
        </Suspense>
      </div>
    )
  }
  const lines = file.content.split('\n')
  return (
    <div className="overflow-auto">
      <pre className="min-w-full text-xs leading-relaxed">
        <code className="grid font-mono">
          {lines.map((line, i) => (
            <span key={i} className="grid grid-cols-[auto_1fr] gap-4 px-4 hover:bg-surface-raised">
              <span className="select-none text-right text-faint">{i + 1}</span>
              <span className="whitespace-pre text-fg">{line || ' '}</span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  )
}

/** La classe de fond d'un point de couleur de groupe (rappel visuel en vue plate). */
function dotClass(tone: Tone): string {
  const map: Record<Tone, string> = {
    ok: 'bg-emerald-500', warn: 'bg-amber-500', danger: 'bg-red-500', info: 'bg-sky-500',
    purple: 'bg-purple-500', accent: 'bg-accent-500', neutral: 'bg-faint',
  }
  return map[tone]
}
