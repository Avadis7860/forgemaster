import { lazy, Suspense, useMemo, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import {
  Alert, Badge, Button, Card, EmptyState, Input, LoadingState, SectionTitle,
} from '@/components/ui'
import { cn } from '@/lib/cn'
import { ApiError } from '@/lib/api'
import { useBundleFile, useBundleTree, useTypes } from '@/lib/queries'
import type { BundleFile, BundleFileEntry } from '@/lib/schemas'
import type { Tone } from '@/lib/statusTone'

// DocView (react-markdown + remark-gfm) en chunk séparé — mêmes économies que DocsTab (lazy, pas dans le
// bundle initial de l'accueil). Un fichier .md du bundle est rendu comme une vraie page ; le reste en mono.
const DocView = lazy(() => import('@/components/docs/DocView').then((m) => ({ default: m.DocView })))

// Taxonomie de curation CÔTÉ FRONT : libellé + tone. Les clés (`group`) sont posées par le serveur
// (`routes/bundles._classify`) ; ici le rôle n'est PLUS un axe de tri concurrent (l'ancien toggle « Curé »)
// mais une **annotation** — une puce de couleur portée par chaque fichier dans l'arbre de dossiers réel.
const GROUPS: ReadonlyArray<{ key: string; label: string; tone: Tone }> = [
  { key: 'method', label: 'Méthode & Persona', tone: 'accent' },
  { key: 'deploy', label: 'Contrat de déploiement', tone: 'info' },
  { key: 'seed', label: 'Seed runnable', tone: 'ok' },
  { key: 'docs', label: 'Docs', tone: 'purple' },
  { key: 'plumbing', label: 'Plomberie', tone: 'neutral' },
]
const toneOf = (group: string): Tone => GROUPS.find((g) => g.key === group)?.tone ?? 'neutral'
const labelOf = (group: string): string => GROUPS.find((g) => g.key === group)?.label ?? group

type Search = { bundle?: string; bfile?: string }

/** Explorer READ-ONLY de l'intérieur des bundles vendorés (grade la surface P5). Ressource GLOBALE (offerte à
 *  la création, partagée par tous les projets) → vit sur le Landing top-level, pas dans un workspace projet.
 *  Piloté par l'URL (`?bundle=&bfile=`) → chaque vue est **deep-linkable** (capturable at-rest par la boucle
 *  visuelle). Deux GET idempotents (arbre, fichier) servent tout — zéro mutation, goto-safe. */
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
  const file = search.bfile ?? null

  const setType = (t: string) =>
    navigate({ to: '/bundles', search: (p) => ({ ...p, bundle: t, bfile: undefined }) })
  const setFile = (f: string | undefined) =>
    navigate({ to: '/bundles', search: (p) => ({ ...p, bfile: f }) })

  return (
    <SectionCard>
      {/* Sélecteur de type — chips (primitive Button). TOUTES en `secondary` → un contour au repos signale
          qu'elles sont cliquables (affordance) ; l'active porte une bordure accent. */}
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Type de bundle">
        {types.map((t) => {
          const active = t.type === activeType
          return (
            <Button
              key={t.type}
              variant="secondary"
              size="sm"
              onClick={() => setType(t.type)}
              aria-pressed={active}
              className={active ? 'border-accent-500 text-fg' : 'text-muted'}
            >
              {t.type}
              <span className="ml-2 text-xs text-faint">v{t.version}</span>
            </Button>
          )
        })}
      </div>

      <p className="text-sm text-muted">
        L'intérieur servi = ce qu'un projet <span className="font-medium text-fg">{activeType}</span> reçoit au seed.
      </p>

      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
        <BundleTreePane type={activeType} selected={file} onOpen={setFile} />
        {/* Volet de lecture : `self-start` → hauteur de contenu (le placeholder reste visible au lieu d'être
            centré dans un panneau étiré à l'arbre) ; `sticky` → il suit le scroll d'un arbre long. */}
        <div className="md:sticky md:top-4 md:self-start">
          <BundleFilePane type={activeType} file={file} />
        </div>
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
        actions={<Badge tone="neutral" className="whitespace-nowrap">lecture seule</Badge>}
      />
      <p className="-mt-2 text-sm text-muted">
        Parcours l'intérieur de chaque bundle vendoré — l'arbre et le corps des fichiers, au-delà des
        métadonnées. Pour juger ce que le cockpit embarque avant de le distribuer.
      </p>
      {children}
    </Card>
  )
}

// ── Arbre de dossiers réel ────────────────────────────────────────────────────────────────────────────────
// Un seul mode : la donnée plate servie (`useBundleTree`, chaque entrée = `{path, group}`) est reconstruite en
// **arbre de répertoires** (le layout disque réel), chaque fichier annoté d'une **puce de rôle** (l'ancien axe
// « Curé » devient une annotation, plus une vue concurrente). Un champ de filtre restreint l'arbre en direct.

type TreeNode = {
  name: string
  path: string
  file: BundleFileEntry | null // non-null = feuille (fichier) ; null = répertoire
  children: TreeNode[]
  count: number // nombre de fichiers-feuilles sous ce nœud (badge de densité sur les dossiers)
}

/** Reconstruit l'arbre de dossiers depuis la liste plate de chemins. Répertoires d'abord, puis fichiers, chaque
 *  strate triée alphabétiquement — l'ordre stable rend la capture at-rest déterministe. */
function buildTree(files: BundleFileEntry[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', file: null, children: [], count: 0 }
  const index = new Map<string, TreeNode>([['', root]])
  for (const f of files) {
    const parts = f.path.split('/')
    let parentPath = ''
    parts.forEach((part, i) => {
      const path = parentPath ? `${parentPath}/${part}` : part
      let node = index.get(path)
      if (!node) {
        node = { name: part, path, file: null, children: [], count: 0 }
        index.set(path, node)
        index.get(parentPath)!.children.push(node)
      }
      if (i === parts.length - 1) node.file = f
      parentPath = path
    })
  }
  const finish = (n: TreeNode): number => {
    n.children.sort((a, b) => {
      const ad = a.file ? 1 : 0
      const bd = b.file ? 1 : 0
      if (ad !== bd) return ad - bd // dossiers (0) avant fichiers (1)
      return a.name.localeCompare(b.name)
    })
    n.count = n.file ? 1 : n.children.reduce((s, c) => s + finish(c), 0)
    return n.count
  }
  finish(root)
  return root.children
}

function BundleTreePane(props: {
  type: string
  selected: string | null
  onOpen: (path: string) => void
}) {
  const { type, selected, onOpen } = props
  const { data, isLoading, isError, error } = useBundleTree(type)
  const [query, setQuery] = useState('')
  // Ensemble des dossiers dont l'utilisateur a INVERSÉ l'état par défaut (XOR ci-dessous). Défaut : seuls les
  // répertoires de premier niveau sont ouverts → un aperçu compact de la forme du bundle, le détail à la demande.
  const [toggled, setToggled] = useState<ReadonlySet<string>>(() => new Set())

  const q = query.trim().toLowerCase()
  const roots = useMemo(() => {
    const files = data?.files ?? []
    const kept = q ? files.filter((f) => f.path.toLowerCase().includes(q)) : files
    return buildTree(kept)
  }, [data?.files, q])

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

  const toggle = (path: string) =>
    setToggled((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  // Ouvert = défaut (premier niveau seulement) XOR inversion utilisateur ; filtrer force l'ouverture (sinon un
  // match sous un dossier replié serait invisible) ; le dossier ancêtre du fichier sélectionné est forcé ouvert
  // (deep-link `bfile` → la feuille surlignée est visible dans l'arbre).
  const isOpen = (path: string) => {
    if (q !== '') return true
    if (selected && selected.startsWith(`${path}/`)) return true
    const defaultOpen = !path.includes('/') // profondeur 0 = répertoire de premier niveau
    return defaultOpen !== toggled.has(path)
  }

  return (
    <div className="space-y-3">
      <Input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Filtrer les fichiers…"
        aria-label="Filtrer les fichiers du bundle"
      />
      <RoleLegend />
      {roots.length === 0 ? (
        <p className="px-2 py-4 text-center text-sm text-faint">Aucun fichier ne correspond à « {query} ».</p>
      ) : (
        <TreeRows nodes={roots} depth={0} isOpen={isOpen} toggle={toggle} selected={selected} onOpen={onOpen} />
      )}
    </div>
  )
}

/** Légende des puces de rôle : décode l'annotation portée par chaque fichier de l'arbre. Ligne discrète
 *  (text-xs faint) — de l'information, pas du chrome. */
function RoleLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-xs text-faint">
      <span>Rôle :</span>
      {GROUPS.map((g) => (
        <span key={g.key} className="flex items-center gap-1">
          <span className={cn('size-1.5 shrink-0 rounded-pill', dotClass(g.tone))} aria-hidden />
          {g.label}
        </span>
      ))}
    </div>
  )
}

/** Rendu récursif d'un niveau de l'arbre. Le guide d'indentation (bordure gauche) est porté par le `<ul>` des
 *  niveaux profonds → l'indentation vient du nesting, pas d'une valeur dynamique (reste tokenisé). */
function TreeRows(props: {
  nodes: TreeNode[]
  depth: number
  isOpen: (path: string) => boolean
  toggle: (path: string) => void
  selected: string | null
  onOpen: (path: string) => void
}) {
  const { nodes, depth, isOpen, toggle, selected, onOpen } = props
  return (
    <ul className={cn('space-y-0.5', depth > 0 && 'ml-3 border-l border-border pl-2')}>
      {nodes.map((node) =>
        node.file ? (
          <li key={node.path}>
            <FileRow
              entry={node.file}
              name={node.name}
              active={selected === node.file.path}
              onClick={() => onOpen(node.file!.path)}
            />
          </li>
        ) : (
          <li key={node.path}>
            <FolderRow node={node} open={isOpen(node.path)} onToggle={() => toggle(node.path)} />
            {isOpen(node.path) && (
              <TreeRows
                nodes={node.children}
                depth={depth + 1}
                isOpen={isOpen}
                toggle={toggle}
                selected={selected}
                onOpen={onOpen}
              />
            )}
          </li>
        ),
      )}
    </ul>
  )
}

/** Une ligne de répertoire : chevron (pivote à l'ouverture) + nom + compte de fichiers sous l'arbre. Primitive
 *  Button ghost (jamais un bouton brut, R1) ; le repli est purement présentation (aucune mutation). */
function FolderRow({ node, open, onToggle }: { node: TreeNode; open: boolean; onToggle: () => void }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onToggle}
      className="w-full justify-start"
      aria-expanded={open}
      title={node.path}
    >
      <span className={cn('shrink-0 text-faint transition-transform', open && 'rotate-90')} aria-hidden>▸</span>
      <span className="shrink-0 text-xs" aria-hidden>📁</span>
      <span className="min-w-0 truncate font-mono text-xs text-fg">{node.name}/</span>
      <span className="ml-auto shrink-0 text-xs text-faint">{node.count}</span>
    </Button>
  )
}

/** Une ligne de fichier : puce de rôle (annotation de curation) + nom (le dossier est implicite par la
 *  position dans l'arbre). Toujours la primitive Button (jamais un bouton brut, R1). */
function FileRow({
  entry, name, active, onClick,
}: { entry: BundleFileEntry; name: string; active: boolean; onClick: () => void }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onClick}
      className={active ? 'w-full justify-start bg-surface-raised text-fg' : 'w-full justify-start'}
      title={`${entry.path} — ${labelOf(entry.group)}`}
    >
      <span className={cn('size-1.5 shrink-0 rounded-pill', dotClass(toneOf(entry.group)))} aria-hidden />
      <span className="shrink-0 text-xs" aria-hidden>📄</span>
      <span className="min-w-0 truncate font-mono text-xs text-fg">{name}</span>
    </Button>
  )
}

/** Le corps du fichier sélectionné : un `.md` rendu en page de doc (DocView), sinon le texte en mono numéroté
 *  (calque de la visionneuse du RepoExplorer). Vide → invite à choisir ; erreur (404 fichier absent) → Alert. */
function BundleFilePane({ type, file }: { type: string; file: string | null }) {
  const { data, isLoading, isError, error } = useBundleFile(type, file ?? '')

  if (!file) {
    // Message nu (pas d'EmptyState bordé) : le volet EST déjà une carte bordée — un cadre interne ferait une
    // double bordure « panneau dans panneau » (gate tissu > panneau).
    return (
      <Card className="flex min-h-48 flex-col items-center justify-center gap-1 p-8 text-center">
        <p className="text-sm font-medium text-fg">Aucun fichier sélectionné</p>
        <p className="max-w-sm text-sm text-muted">Choisis un fichier dans l'arbre pour lire son corps.</p>
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

/** La classe de fond d'une puce de couleur de rôle (annotation de curation portée par chaque fichier). */
function dotClass(tone: Tone): string {
  const map: Record<Tone, string> = {
    ok: 'bg-emerald-500', warn: 'bg-amber-500', danger: 'bg-red-500', info: 'bg-sky-500',
    purple: 'bg-purple-500', accent: 'bg-accent-500', neutral: 'bg-faint',
  }
  return map[tone]
}
