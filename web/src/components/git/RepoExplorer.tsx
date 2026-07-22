import { lazy, Suspense, useState } from 'react'
import { Alert, Badge, Button, Card, EmptyState, LoadingState } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useGitBlob, useGitHistory, useGitTree } from '@/lib/queries'
import type { GitBlob, GitBranch, GitTreeEntry } from '@/lib/schemas'

// DocView (react-markdown + remark-gfm) en chunk séparé — même économie que Bundles/Docs (lazy, hors bundle
// initial). Un `.md` du dépôt (fichier sélectionné ou README auto d'un dossier) est rendu en vraie page.
const DocView = lazy(() => import('@/components/docs/DocView').then((m) => ({ default: m.DocView })))

/** Explorateur de dépôt read-only : sélecteur de réf + arbre navigable (dossiers d'abord, breadcrumb) +
 *  visionneuse de fichier (n° de ligne). Zéro mutation — deux GET idempotents (arbre, blob) servent la vue,
 *  atteignables par le runner de boucle visuelle goto-only sans risque. Greffé sous la vue synchro de l'onglet
 *  Git. `branches` est réutilisé de la vue parente (aucune requête de plus pour peupler le sélecteur). */
export function RepoExplorer({ project, branches }: { project: string; branches: GitBranch[] }) {
  const refs = branches.map((b) => b.name)
  const [ref, setRef] = useState(refs.includes('dev') ? 'dev' : (refs[0] ?? 'dev'))
  const [path, setPath] = useState('')          // dossier courant ('' = racine)
  const [file, setFile] = useState<string | null>(null)  // fichier sélectionné (chemin complet)

  // Changer de réf remet la navigation à zéro (un chemin peut ne pas exister à une autre réf).
  function pickRef(next: string) {
    setRef(next)
    setPath('')
    setFile(null)
  }

  const join = (dir: string, name: string) => (dir ? `${dir}/${name}` : name)

  // README auto du dossier courant (façon GitHub : mener par le contenu quand aucun fichier n'est sélectionné).
  // `useGitTree` partage la clé React-Query de TreePane → aucun fetch de plus. Détection insensible à la casse.
  const { data: tree } = useGitTree(project, ref, path)
  const readmeEntry = tree?.entries.find((e) => e.type === 'blob' && e.name.toLowerCase() === 'readme.md')
  const readmePath = readmeEntry ? join(path, readmeEntry.name) : null

  return (
    <Card className="space-y-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-medium text-fg">Fichiers</p>
        <label className="flex items-center gap-2 text-sm text-muted">
          <span>Réf</span>
          <select
            value={ref}
            onChange={(e) => pickRef(e.target.value)}
            className="h-8 rounded-card border border-border bg-surface-raised px-2 text-sm text-fg"
          >
            {refs.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
      </div>

      <Breadcrumb path={path} onGo={(p) => { setPath(p); setFile(null) }} />

      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <TreePane
          project={project}
          gitRef={ref}
          path={path}
          selected={file}
          onOpenDir={(name) => { setPath(join(path, name)); setFile(null) }}
          onOpenFile={(name) => setFile(join(path, name))}
        />
        {file == null && readmePath
          ? <ReadmePane project={project} gitRef={ref} readmePath={readmePath} />
          : <FilePane project={project} gitRef={ref} file={file} />}
      </div>
    </Card>
  )
}

/** Fil d'Ariane du chemin courant : racine + chaque segment cliquable pour remonter à ce niveau. */
function Breadcrumb({ path, onGo }: { path: string; onGo: (p: string) => void }) {
  const parts = path ? path.split('/') : []
  return (
    <div className="flex flex-wrap items-center gap-1 text-sm">
      <Button variant="ghost" size="sm" onClick={() => onGo('')} disabled={!path}>racine</Button>
      {parts.map((seg, i) => {
        const upto = parts.slice(0, i + 1).join('/')
        const last = i === parts.length - 1
        return (
          <span key={upto} className="flex items-center gap-1">
            <span className="text-faint">/</span>
            <Button variant="ghost" size="sm" onClick={() => onGo(upto)} disabled={last}>{seg}</Button>
          </span>
        )
      })}
    </div>
  )
}

/** Arbre du dossier courant : dossiers d'abord (l'API trie), chaque entrée passe par la primitive Button
 *  (jamais un bouton HTML brut, cf. R1). Un fichier sélectionné est mis en avant. */
function TreePane(props: {
  project: string
  gitRef: string
  path: string
  selected: string | null
  onOpenDir: (name: string) => void
  onOpenFile: (name: string) => void
}) {
  const { project, gitRef, path, selected, onOpenDir, onOpenFile } = props
  const { data, isLoading, isError, error } = useGitTree(project, gitRef, path)

  if (isLoading) return <LoadingState label="Lecture de l'arbre…" />
  if (isError || !data) {
    return (
      <Alert tone="danger" title="Arbre indisponible">
        {error instanceof ApiError ? error.detail : String(error)}
      </Alert>
    )
  }
  if (data.entries.length === 0) {
    return <EmptyState title="Dossier vide" description="Aucune entrée à cette réf." />
  }
  const full = (name: string) => (path ? `${path}/${name}` : name)
  return (
    <ul className="space-y-0.5">
      {data.entries.map((e) => (
        <li key={e.name}>
          <EntryRow
            entry={e}
            active={e.type === 'blob' && selected === full(e.name)}
            onClick={() => (e.type === 'tree' ? onOpenDir(e.name) : e.type === 'blob' && onOpenFile(e.name))}
          />
        </li>
      ))}
    </ul>
  )
}

/** Une ligne d'entrée : icône de type + nom + taille (fichiers). Sous-modules (commit) non navigables. */
function EntryRow({ entry, active, onClick }: { entry: GitTreeEntry; active: boolean; onClick: () => void }) {
  const isDir = entry.type === 'tree'
  const isSub = entry.type === 'commit'
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onClick}
      disabled={isSub}
      className={active ? 'w-full justify-start bg-surface-raised text-fg' : 'w-full justify-start'}
    >
      <span className="w-4 shrink-0 text-center text-muted">{isDir ? '📁' : isSub ? '↪' : '📄'}</span>
      <span className="truncate">{entry.name}</span>
      {entry.type === 'blob' && entry.size != null && (
        <span className="ml-auto shrink-0 pl-2 text-xs text-faint">{fmtSize(entry.size)}</span>
      )}
    </Button>
  )
}

/** Visionneuse du fichier sélectionné : contenu texte avec n° de ligne, ou état binaire / trop-gros / vide.
 *  Un basculeur « Historique » ouvre les commits touchant ce fichier (intelligence git P3). */
function FilePane({ project, gitRef, file }: { project: string; gitRef: string; file: string | null }) {
  const [showHistory, setShowHistory] = useState(false)
  const { data, isLoading, isError, error } = useGitBlob(project, gitRef, file ?? '')

  // Vide/chargement = TISSU, pas un carton dans le carton « Fichiers » (gate tissu > panneau) : la cellule
  // droite reste un espace, pas un panneau bordé. Le relief (Card) est réservé au CONTENU réel du fichier.
  if (!file) {
    return (
      <div className="flex min-h-40 items-center justify-center">
        <EmptyState title="Aucun fichier" description="Choisis un fichier dans l'arbre pour l'afficher." />
      </div>
    )
  }
  if (isLoading) {
    return <div className="flex min-h-40 items-center justify-center"><LoadingState label="Lecture du fichier…" /></div>
  }
  if (isError || !data) {
    return (
      <div className="min-h-40">
        <Alert tone="danger" title="Fichier indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      </div>
    )
  }
  return (
    <Card className="flex min-h-40 flex-col overflow-hidden p-0">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2">
        <code className="min-w-0 flex-1 truncate font-mono text-xs text-fg" title={file}>{file}</code>
        <Button variant="ghost" size="sm" onClick={() => setShowHistory((v) => !v)}
          className={showHistory ? 'shrink-0 bg-surface-raised text-fg' : 'shrink-0'}>Historique</Button>
        <span className="shrink-0 text-xs text-faint">{fmtSize(data.size)}</span>
      </div>
      {showHistory
        ? <FileHistory project={project} gitRef={gitRef} file={file} />
        : <FileBody blob={data} />}
    </Card>
  )
}

/** README auto-rendu du dossier courant quand aucun fichier n'est sélectionné (façon GitHub : le contenu
 *  mène, pas un vide). GET blob idempotent → Markdown via DocView (lazy). Un README non affichable
 *  (binaire / trop-gros / vide) retombe silencieusement sur l'invite standard — jamais un écran d'erreur. */
function ReadmePane({ project, gitRef, readmePath }: { project: string; gitRef: string; readmePath: string }) {
  const { data, isLoading, isError, error } = useGitBlob(project, gitRef, readmePath)

  if (isLoading) {
    return <div className="flex min-h-40 items-center justify-center"><LoadingState label="Lecture du README…" /></div>
  }
  if (isError || !data) {
    return (
      <div className="min-h-40">
        <Alert tone="danger" title="README indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      </div>
    )
  }
  if (data.too_large || data.binary || !data.content.trim()) {
    return (
      <div className="flex min-h-40 items-center justify-center">
        <EmptyState title="Aucun fichier" description="Choisis un fichier dans l'arbre pour l'afficher." />
      </div>
    )
  }
  return (
    <Card className="flex min-h-40 flex-col overflow-hidden p-0">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <span className="w-4 shrink-0 text-center text-muted">📄</span>
        <code className="min-w-0 flex-1 truncate font-mono text-xs text-fg" title={readmePath}>
          {readmePath.split('/').pop()}
        </code>
      </div>
      {data.truncated && (
        <div className="px-4 py-2"><Badge tone="warn" dot>Aperçu tronqué</Badge></div>
      )}
      <div className="overflow-auto p-5">
        <Suspense fallback={<LoadingState label="Rendu Markdown…" />}>
          <DocView content={data.content} />
        </Suspense>
      </div>
    </Card>
  )
}

/** Historique des commits touchant le fichier ouvert (récents d'abord). Un GET idempotent (`useGitHistory`)
 *  activé seulement quand le panneau est déplié — n° court + auteur + date + sujet. */
function FileHistory({ project, gitRef, file }: { project: string; gitRef: string; file: string }) {
  const { data, isLoading, isError, error } = useGitHistory(project, gitRef, file)

  if (isLoading) return <div className="p-5"><LoadingState label="Lecture de l'historique…" /></div>
  if (isError || !data) {
    return (
      <div className="p-5">
        <Alert tone="danger" title="Historique indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      </div>
    )
  }
  if (data.commits.length === 0) {
    return <div className="p-5"><EmptyState title="Aucun historique"
      description="Ce fichier n'a aucun commit à cette réf." /></div>
  }
  return (
    <ol className="divide-y divide-border">
      {data.commits.map((c) => (
        <li key={c.sha} className="flex items-baseline gap-3 px-4 py-2">
          <code className="shrink-0 font-mono text-xs text-muted">{c.short}</code>
          <span className="min-w-0 flex-1 truncate text-sm text-fg" title={c.subject}>{c.subject}</span>
          <span className="shrink-0 text-xs text-faint">{c.author}</span>
        </li>
      ))}
    </ol>
  )
}

/** Corps de la visionneuse : les gardes L4 d'abord (binaire / trop gros), sinon le texte numéroté. */
function FileBody({ blob }: { blob: GitBlob }) {
  if (blob.too_large) {
    return (
      <div className="p-5">
        <EmptyState title="Fichier trop volumineux"
          description={`${fmtSize(blob.size)} — au-delà de la limite d'affichage, contenu non chargé.`} />
      </div>
    )
  }
  if (blob.binary) {
    return (
      <div className="p-5">
        <EmptyState title="Fichier binaire" description={`${fmtSize(blob.size)} — pas d'aperçu texte.`} />
      </div>
    )
  }
  if (blob.path.endsWith('.md')) {
    // `.md` → Markdown en réutilisant DocView (calque BundleFileBody, pas de 2ᵉ renderer). Never-silent-cap :
    // un aperçu tronqué reste signalé au-dessus du rendu.
    return (
      <div className="flex flex-col">
        {blob.truncated && (
          <div className="px-4 py-2"><Badge tone="warn" dot>Aperçu tronqué</Badge></div>
        )}
        <div className="overflow-auto p-5">
          <Suspense fallback={<LoadingState label="Rendu Markdown…" />}>
            <DocView content={blob.content} />
          </Suspense>
        </div>
      </div>
    )
  }
  const lines = blob.content.split('\n')
  return (
    <div className="flex flex-col">
      {blob.truncated && (
        <div className="px-4 py-2">
          <Badge tone="warn" dot>Aperçu tronqué</Badge>
        </div>
      )}
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
    </div>
  )
}

/** Taille lisible : octets sous 1 Ko, sinon Ko/Mo à une décimale. */
function fmtSize(n: number): string {
  if (n < 1024) return `${n} o`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} Ko`
  return `${(n / (1024 * 1024)).toFixed(1)} Mo`
}
