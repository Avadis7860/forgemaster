import { lazy, Suspense, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { Alert, Badge, Button, Card, EmptyState, LoadingState } from '@/components/ui'
import { ApiError, gitDownloadUrl, gitRawUrl } from '@/lib/api'
import { timeAgo } from '@/lib/git'
import { useGitBlob, useGitHistory, useGitTree } from '@/lib/queries'
import type { GitBlob, GitBranch, GitTree, GitTreeEntry } from '@/lib/schemas'

// DocView (react-markdown + remark-gfm) en chunk séparé — même économie que Bundles/Docs (lazy, hors bundle
// initial). Un `.md` du dépôt (fichier sélectionné ou README auto d'un dossier) est rendu en vraie page.
const DocView = lazy(() => import('@/components/docs/DocView').then((m) => ({ default: m.DocView })))
// Visionneuse de code colorée (lowlight) — chunk séparé, ne charge lowlight que quand on ouvre un fichier
// de code dont l'extension est reconnue (cf. extToLang).
const HighlightedCode = lazy(() => import('./HighlightedCode'))

/** Explorateur de dépôt read-only : sélecteur de réf + arbre navigable (dossiers d'abord, breadcrumb) +
 *  visionneuse de fichier (n° de ligne). Zéro mutation — deux GET idempotents (arbre, blob) servent la vue,
 *  atteignables par le runner de boucle visuelle goto-only sans risque. Greffé sous la vue synchro de l'onglet
 *  Git. `branches`+`tags` sont réutilisés de la vue parente (aucune requête de plus pour peupler le sélecteur). */
type Search = { ref?: string; path?: string; file?: string }

export function RepoExplorer(
  { project, branches, tags }: { project: string; branches: GitBranch[]; tags: GitBranch[] },
) {
  const branchNames = branches.map((b) => b.name)
  const allRefs = [...branches, ...tags]          // branches ∪ tags : validité de réf + headSha (permalink)
  const refNames = allRefs.map((b) => b.name)
  const search = useSearch({ strict: false }) as Search
  const navigate = useNavigate()

  // État {ref, path, file} porté par l'URL (deep-linkable, calque BundleExplorer/GitSurface) → chaque vue
  // fichier est partageable et self-verifiable. Défaut de réf : `dev` sinon la 1ʳᵉ branche (jamais un tag ni
  // une réf absente).
  const ref = search.ref && refNames.includes(search.ref)
    ? search.ref
    : (branchNames.includes('dev') ? 'dev' : (branchNames[0] ?? 'dev'))
  const path = search.path ?? ''                 // dossier courant ('' = racine)
  const file = search.file ?? null               // fichier sélectionné (chemin complet)
  // SHA de HEAD de la réf courante (branche OU tag) → permalink épinglé au commit (immuable, façon « y »).
  const headSha = allRefs.find((b) => b.name === ref)?.sha

  // Setters via l'URL (updater fonctionnel → préserve project/view/sha) : changer de réf remet path+file à zéro
  // (un chemin peut ne pas exister à une autre réf) ; changer de dossier déselectionne le fichier.
  const pickRef = (next: string) =>
    navigate({ to: '/git', search: (p) => ({ ...p, ref: next, path: undefined, file: undefined }) })
  const goPath = (next: string) =>
    navigate({ to: '/git', search: (p) => ({ ...p, path: next || undefined, file: undefined }) })
  const openFile = (next: string) =>
    navigate({ to: '/git', search: (p) => ({ ...p, file: next }) })

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
            <optgroup label="Branches">
              {branches.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}
            </optgroup>
            {tags.length > 0 && (
              <optgroup label="Tags">
                {tags.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
              </optgroup>
            )}
          </select>
        </label>
      </div>

      <Breadcrumb path={path} onGo={goPath} />

      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <TreePane
          project={project}
          gitRef={ref}
          path={path}
          selected={file}
          onOpenDir={(name) => goPath(join(path, name))}
          onOpenFile={(name) => openFile(join(path, name))}
        />
        {file == null && readmePath
          ? <ReadmePane project={project} gitRef={ref} readmePath={readmePath} />
          : <FilePane project={project} gitRef={ref} file={file} headSha={headSha} />}
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
    <div className="space-y-2">
      {data.latest_commit && <LatestCommitBar latest={data.latest_commit} />}
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
    </div>
  )
}

/** Barre « latest commit » au-dessus de l'arbre (façon GitHub) : auteur · sha court · sujet · âge · nb commits.
 *  Coiffe la liste — elle mène le dossier au lieu d'un vide. Calque la ligne méta commit de GitIntelligence. */
function LatestCommitBar({ latest }: { latest: NonNullable<GitTree['latest_commit']> }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md bg-surface-raised px-3 py-2 text-xs text-muted">
      <span className="font-medium text-fg">{latest.author}</span>
      <code className="font-mono text-faint">{latest.short}</code>
      <span className="min-w-0 flex-1 truncate" title={latest.subject}>{latest.subject}</span>
      <span className="shrink-0 text-faint" title={new Date(latest.date).toLocaleString()}>
        {timeAgo(latest.date)}
      </span>
      <span className="shrink-0 text-faint">· {latest.count} commit{latest.count > 1 ? 's' : ''}</span>
    </div>
  )
}

/** Une ligne d'entrée façon GitHub, 3 colonnes : `icône+nom · sujet du dernier commit · âge relatif`. La ligne
 *  reste un `Button` (surface cliquable, R1) ; une grille interne aligne les colonnes d'une rangée à l'autre.
 *  La taille quitte la liste (parité GitHub — elle vit dans l'en-tête de la vue fichier). Sous-modules (commit)
 *  non navigables. `last_commit` peut manquer (daemon ancien / entrée sans commit) → colonnes vides. */
function EntryRow({ entry, active, onClick }: { entry: GitTreeEntry; active: boolean; onClick: () => void }) {
  const isDir = entry.type === 'tree'
  const isSub = entry.type === 'commit'
  const lc = entry.last_commit
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onClick}
      disabled={isSub}
      className={active ? 'w-full justify-start bg-surface-raised text-fg' : 'w-full justify-start'}
    >
      <span className="grid w-full grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)_auto] items-center gap-3">
        <span className="flex min-w-0 items-center gap-2">
          <span className="w-4 shrink-0 text-center text-muted">{isDir ? '📁' : isSub ? '↪' : '📄'}</span>
          <span className="truncate">{entry.name}</span>
        </span>
        <span className="truncate text-left text-xs text-muted" title={lc?.subject}>{lc?.subject ?? ''}</span>
        {lc?.date
          ? <span className="shrink-0 text-xs text-faint" title={new Date(lc.date).toLocaleString()}>
              {timeAgo(lc.date)}
            </span>
          : <span />}
      </span>
    </Button>
  )
}

/** Visionneuse du fichier sélectionné : contenu texte avec n° de ligne, ou état binaire / trop-gros / vide.
 *  Un basculeur « Historique » ouvre les commits touchant ce fichier (intelligence git P3). */
function FilePane({ project, gitRef, file, headSha }: {
  project: string; gitRef: string; file: string | null; headSha?: string
}) {
  const [showHistory, setShowHistory] = useState(false)
  const [contentCopied, copyContent] = useCopy()
  const [linkCopied, copyLink] = useCopy()
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
  // Nb de lignes : dérivé du contenu (indispo pour un binaire/trop-gros, où content est vide) — pas de faux 0.
  const lineCount = !data.binary && !data.too_large && data.content ? data.content.split('\n').length : null
  const rawUrl = gitRawUrl(project, gitRef, file)
  const downloadUrl = gitDownloadUrl(project, gitRef, file)
  const permalink = filePermalink(project, headSha ?? gitRef, file)

  return (
    <Card className="flex min-h-40 flex-col overflow-hidden p-0">
      {/* En-tête 2 rangées (façon GitHub) : le chemin en pleine largeur (ne s'écrase pas sous les actions),
          puis méta (taille · lignes) + barre d'actions. */}
      <div className="flex flex-col gap-1.5 border-b border-border px-4 py-2">
        <code className="truncate font-mono text-xs text-fg" title={file}>{file}</code>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-xs text-faint">
            {fmtSize(data.size)}{lineCount != null ? ` · ${lineCount} ligne${lineCount > 1 ? 's' : ''}` : ''}
          </span>
          {/* Actions façon GitHub : Historique · Raw · Copy · Download · Permalink. Toutes en primitive
              Button (R1/R2, zéro `<a>` stylé) — Raw ouvre le flux inline dans un onglet, Download déclenche
              l'enregistrement (Content-Disposition backend), Copy/Permalink passent par le presse-papier. */}
          <div className="ml-auto flex flex-wrap items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => setShowHistory((v) => !v)}
              className={showHistory ? 'bg-surface-raised text-fg' : undefined}>Historique</Button>
            <Button variant="ghost" size="sm" title="Ouvrir le contenu brut dans un onglet"
              onClick={() => window.open(rawUrl, '_blank', 'noopener,noreferrer')}>Raw</Button>
            <Button variant="ghost" size="sm" title="Copier le contenu du fichier"
              disabled={data.binary || data.too_large}
              onClick={() => copyContent(data.content)}>{contentCopied ? 'Copié' : 'Copy'}</Button>
            <Button variant="ghost" size="sm" title="Télécharger le fichier"
              onClick={() => triggerDownload(downloadUrl)}>Download</Button>
            <Button variant="ghost" size="sm" title="Copier un lien permanent (épinglé au commit)"
              onClick={() => copyLink(permalink)}>{linkCopied ? 'Lien copié' : 'Permalink'}</Button>
          </div>
        </div>
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
          <span className="shrink-0 text-xs text-faint" title={new Date(c.date).toLocaleString()}>
            {timeAgo(c.date)}
          </span>
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
  const lang = extToLang(blob.path)
  if (lang) {
    // Fichier de code à extension reconnue → coloration lowlight (chunk lazy). Never-silent-cap : troncature signalée.
    return (
      <div className="flex flex-col">
        {blob.truncated && (
          <div className="px-4 py-2"><Badge tone="warn" dot>Aperçu tronqué</Badge></div>
        )}
        <Suspense fallback={<div className="p-4"><LoadingState label="Coloration…" /></div>}>
          <HighlightedCode content={blob.content} lang={lang} />
        </Suspense>
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

/** Extension de fichier → langage lowlight (sous-ensemble `common`). Inconnu → '' : rendu texte nu, lowlight
 *  non chargé (les fichiers sans langage reconnu ne tirent pas le chunk de coloration). */
function extToLang(path: string): string {
  const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase()
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript', mts: 'typescript', cts: 'typescript',
    js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
    py: 'python', rb: 'ruby', go: 'go', rs: 'rust', java: 'java', cs: 'csharp',
    c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp',
    php: 'php', swift: 'swift', kt: 'kotlin', lua: 'lua', r: 'r', pl: 'perl',
    sh: 'bash', bash: 'bash', zsh: 'bash',
    json: 'json', yml: 'yaml', yaml: 'yaml', toml: 'ini', ini: 'ini', cfg: 'ini',
    css: 'css', scss: 'scss', less: 'less',
    html: 'xml', htm: 'xml', xml: 'xml', svg: 'xml',
    sql: 'sql', graphql: 'graphql', gql: 'graphql', diff: 'diff', patch: 'diff',
  }
  return map[ext] ?? ''
}

/** Copie dans le presse-papier avec accusé transitoire (patron TemplateExplorer) : dégrade en silence si
 *  l'API clipboard est indisponible (contexte non sécurisé). Retourne `[copied, copy]`. */
function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false)
  const copy = (text: string) => {
    void (async () => {
      try {
        await navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      } catch {
        /* clipboard indisponible (contexte non sécurisé) — no-op silencieux */
      }
    })()
  }
  return [copied, copy]
}

/** Déclenche le téléchargement d'une URL (le backend force `Content-Disposition: attachment`) via un ancrage
 *  transitoire — évite un `<a>` stylé (R2) tout en gardant la sémantique « enregistrer le fichier ». */
function triggerDownload(url: string) {
  const a = document.createElement('a')
  a.href = url
  a.rel = 'noopener'
  a.click()
}

/** Permalink absolu épinglé au SHA (immuable, façon « y » de GitHub) : reproduit la vue fichiers courante avec
 *  la réf résolue en commit → copiable/partageable tel quel. Le dossier est dérivé du chemin du fichier. */
function filePermalink(project: string, pinnedRef: string, file: string): string {
  const dir = file.includes('/') ? file.slice(0, file.lastIndexOf('/')) : ''
  const u = new URL(`${window.location.origin}/git`)
  u.searchParams.set('project', project)
  u.searchParams.set('view', 'fichiers')
  u.searchParams.set('ref', pinnedRef)
  if (dir) u.searchParams.set('path', dir)
  u.searchParams.set('file', file)
  return u.toString()
}

/** Taille lisible : octets sous 1 Ko, sinon Ko/Mo à une décimale. */
function fmtSize(n: number): string {
  if (n < 1024) return `${n} o`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} Ko`
  return `${(n / (1024 * 1024)).toFixed(1)} Mo`
}
