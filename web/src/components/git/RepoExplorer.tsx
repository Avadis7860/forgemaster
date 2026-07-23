import { lazy, Suspense, useEffect, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { Alert, Badge, Button, Card, Dialog, EmptyState, Input, LoadingState } from '@/components/ui'
import { ApiError, gitDownloadUrl, gitRawUrl } from '@/lib/api'
import { fuzzyFilter, timeAgo } from '@/lib/git'
import { useScrollToLine } from '@/lib/useScrollToLine'
import { useGitBlame, useGitBlob, useGitHistory, useGitPaths, useGitSearch, useGitTree } from '@/lib/queries'
import type { BlameLine, GitBlob, GitBranch, GitSearchMatch, GitTree, GitTreeEntry } from '@/lib/schemas'

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
type Search = { ref?: string; path?: string; file?: string; line?: number }

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
  // Ligne à mettre en avant (deep-link depuis la recherche / permalink) : 1-based, coercée depuis l'URL
  // (sans `validateSearch`, la valeur peut revenir en chaîne au rechargement). NaN/≤0 → aucune ligne.
  const line = search.line != null && Number.isFinite(Number(search.line)) && Number(search.line) > 0
    ? Number(search.line)
    : undefined
  // SHA de HEAD de la réf courante (branche OU tag) → permalink épinglé au commit (immuable, façon « y »).
  const headSha = allRefs.find((b) => b.name === ref)?.sha

  // Setters via l'URL (updater fonctionnel → préserve project/view/sha) : changer de réf remet path+file à zéro
  // (un chemin peut ne pas exister à une autre réf) ; changer de dossier déselectionne le fichier. `line` ne
  // survit qu'à un ciblage explicite (recherche) — toute autre navigation la remet à zéro (pas de surbrillance
  // fantôme sur un fichier ouvert autrement).
  const pickRef = (next: string) =>
    navigate({ to: '/git', search: (p) => ({ ...p, ref: next, path: undefined, file: undefined, line: undefined }) })
  const goPath = (next: string) =>
    navigate({ to: '/git', search: (p) => ({ ...p, path: next || undefined, file: undefined, line: undefined }) })
  const openFile = (next: string) =>
    navigate({ to: '/git', search: (p) => ({ ...p, file: next, line: undefined }) })
  // Palette « go to file » (line absent → surbrillance effacée) ET recherche de code (line ciblée) : ouvre un
  // fichier ET aligne l'arbre sur son dossier (path=dir, file=chemin complet).
  const openFileAt = (full: string, line?: number) => {
    const dir = full.includes('/') ? full.slice(0, full.lastIndexOf('/')) : ''
    navigate({ to: '/git', search: (p) => ({ ...p, path: dir || undefined, file: full, line }) })
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
        <div className="flex items-center gap-3">
          <p className="text-sm font-medium text-fg">Fichiers</p>
          <GoToFilePalette project={project} gitRef={ref} onPick={openFileAt} />
          <SearchPalette project={project} gitRef={ref} onPick={openFileAt} />
        </div>
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
          : <FilePane project={project} gitRef={ref} file={file} headSha={headSha} highlightLine={line} />}
      </div>
    </Card>
  )
}

/** Palette « go to file » (façon GitHub `t`) : ouvre un Dialog avec un champ de filtre fuzzy sur la liste
 *  plate des fichiers de la réf (tirée paresseusement à l'ouverture). Sélectionner un résultat ouvre le
 *  fichier (deep-link). `truncated` du serveur (cap signalé) est affiché — jamais un « tout » trompeur. */
function GoToFilePalette(
  { project, gitRef, onPick }: { project: string; gitRef: string; onPick: (path: string) => void },
) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const { data, isLoading } = useGitPaths(project, gitRef, open)
  const results = data ? fuzzyFilter(data.paths, query) : []
  const pick = (path: string) => { onPick(path); setOpen(false); setQuery('') }

  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => { setQuery(''); setOpen(true) }}>Go to file</Button>
      <Dialog open={open} onOpenChange={setOpen} title="Aller au fichier">
        <div className="space-y-3">
          <Input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filtrer les fichiers…"
            aria-label="Filtrer les fichiers"
          />
          {isLoading ? (
            <LoadingState label="Lecture de l'arbre…" />
          ) : results.length === 0 ? (
            <EmptyState title="Aucun fichier" description="Aucun chemin ne correspond à ce filtre." />
          ) : (
            <ul className="max-h-[50vh] space-y-0.5 overflow-auto">
              {data?.truncated && (
                <li className="px-2 py-1">
                  <Badge tone="warn">liste tronquée au cap serveur — affine le filtre</Badge>
                </li>
              )}
              {results.map((path) => (
                <li key={path}>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start truncate font-mono text-xs"
                    onClick={() => pick(path)}
                  >
                    {path}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Dialog>
    </>
  )
}

/** Palette « rechercher dans le code » : recherche plein-texte (grep serveur) sur le contenu des fichiers à la
 *  réf courante. Requête DÉBOUNCÉE (≈200 ms → une requête par pause de frappe, pas par touche) ; hook `enabled`
 *  seulement palette ouverte + requête non vide (fetch paresseux). Chaque correspondance `chemin:ligne` + extrait
 *  est un Button (R1) qui ouvre le fichier À la ligne (deep-link E.2). `truncated`/`count` du serveur affichés —
 *  cap SIGNALÉ, jamais un « tout » trompeur. */
function SearchPalette(
  { project, gitRef, onPick }: { project: string; gitRef: string; onPick: (path: string, line: number) => void },
) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const q = useDebounced(query, 200).trim()
  const { data, isLoading } = useGitSearch(project, gitRef, q, open)
  const pick = (m: GitSearchMatch) => { onPick(m.path, m.line); setOpen(false); setQuery('') }

  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => { setQuery(''); setOpen(true) }}>Rechercher</Button>
      <Dialog open={open} onOpenChange={setOpen} title="Rechercher dans le code">
        <div className="space-y-3">
          <Input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher une chaîne…"
            aria-label="Rechercher une chaîne dans le code"
          />
          {!q ? (
            <EmptyState title="Rechercher dans le code"
              description="Saisis une chaîne — la recherche porte sur le contenu des fichiers à cette réf." />
          ) : isLoading ? (
            <LoadingState label="Recherche…" />
          ) : !data || data.results.length === 0 ? (
            <EmptyState title="Aucune correspondance" description={`Aucune ligne ne contient « ${q} ».`} />
          ) : (
            <ul className="max-h-[50vh] space-y-0.5 overflow-auto">
              {data.truncated && (
                <li className="px-2 py-1">
                  <Badge tone="warn">
                    {data.count} correspondances — {data.results.length} premières affichées, affine la recherche
                  </Badge>
                </li>
              )}
              {data.results.map((m, i) => (
                <li key={`${m.path}:${m.line}:${i}`}>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start gap-2 font-mono text-xs"
                    onClick={() => pick(m)}
                  >
                    <span className="shrink-0 text-muted">{m.path}:{m.line}</span>
                    <span className="min-w-0 truncate text-faint">{m.text.trim()}</span>
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Dialog>
    </>
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
function FilePane({ project, gitRef, file, headSha, highlightLine }: {
  project: string; gitRef: string; file: string | null; headSha?: string; highlightLine?: number
}) {
  const [showHistory, setShowHistory] = useState(false)
  const [showBlame, setShowBlame] = useState(false)
  const [contentCopied, copyContent] = useCopy()
  const [linkCopied, copyLink] = useCopy()
  const { data, isLoading, isError, error } = useGitBlob(project, gitRef, file ?? '')
  // Blame paresseux : tiré seulement quand le toggle est actif. History et Blame sont des vues exclusives.
  const { data: blame } = useGitBlame(project, gitRef, file ?? '', showBlame)

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
  const permalink = filePermalink(project, headSha ?? gitRef, file, highlightLine)

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
            <Button variant="ghost" size="sm"
              onClick={() => { setShowHistory((v) => !v); setShowBlame(false) }}
              className={showHistory ? 'bg-surface-raised text-fg' : undefined}>Historique</Button>
            <Button variant="ghost" size="sm" title="Attribuer chaque ligne à son commit (blame)"
              onClick={() => { setShowBlame((v) => !v); setShowHistory(false) }}
              className={showBlame ? 'bg-surface-raised text-fg' : undefined}>Blame</Button>
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
        : <FileBody blob={data} blame={showBlame ? blame?.lines : undefined} highlightLine={highlightLine} />}
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

/** Cellule de gouttière blame d'une ligne : `sha court · âge` affichés UNE fois par run de même commit
 *  (collapse façon GitHub) ; auteur + résumé en infobulle. Vide si la ligne n'a pas de blame (ex. dernière
 *  ligne vide d'un fichier terminé par `\n`). */
function BlameCell({ line, prev }: { line?: BlameLine; prev?: BlameLine }) {
  if (!line) return <span aria-hidden className="select-none" />
  const first = line.sha !== prev?.sha        // 1ʳᵉ ligne d'un run de même commit → on montre l'attribution
  return (
    <span
      className="select-none truncate pr-2 text-faint"
      title={first ? `${line.author} · ${line.summary}` : undefined}
    >
      {first ? `${line.sha.slice(0, 7)} · ${timeAgo(line.date)}` : ''}
    </span>
  )
}

/** Corps de la visionneuse : les gardes L4 d'abord (binaire / trop gros), puis — si `blame` est fourni — les
 *  lignes brutes numérotées avec gouttière blame (façon GitHub : le blame prime sur la coloration, non coloré),
 *  sinon le rendu normal (Markdown / code coloré / texte numéroté). */
function FileBody(
  { blob, blame, highlightLine }: { blob: GitBlob; blame?: BlameLine[]; highlightLine?: number },
) {
  // Scroll+surbrillance de la ligne ciblée (recherche/permalink) : ref posée sur le wrapper scrollable du rendu
  // texte nu (le rendu code coloré gère la sienne dans HighlightedCode). Hook appelé avant tout early-return.
  const scrollRef = useScrollToLine(highlightLine, blob.content)
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
  if (blame) {
    // Mode blame : lignes brutes numérotées + gouttière (sha court · âge, collapsé par run de commit).
    const blameLines = blob.content.split('\n')
    return (
      <div className="flex flex-col">
        {blob.truncated && (
          <div className="px-4 py-2"><Badge tone="warn" dot>Aperçu tronqué</Badge></div>
        )}
        <div className="overflow-auto">
          <pre className="min-w-full text-xs leading-relaxed">
            <code className="grid font-mono">
              {blameLines.map((line, i) => (
                <span key={i} className="grid grid-cols-[10rem_auto_1fr] gap-4 px-4 hover:bg-surface-raised">
                  <BlameCell line={blame[i]} prev={blame[i - 1]} />
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
  if (blob.path.endsWith('.md') && highlightLine == null) {
    // `.md` → Markdown en réutilisant DocView (calque BundleFileBody, pas de 2ᵉ renderer). Never-silent-cap :
    // un aperçu tronqué reste signalé au-dessus du rendu. Exception : un deep-link ciblant une LIGNE (recherche)
    // retombe sur le rendu texte nu ci-dessous — une ligne n'a de sens que dans la source, pas le Markdown rendu.
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
          <HighlightedCode content={blob.content} lang={lang} highlightLine={highlightLine} />
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
      <div ref={scrollRef} className="overflow-auto">
        <pre className="min-w-full text-xs leading-relaxed">
          <code className="grid font-mono">
            {lines.map((line, i) => (
              <span key={i} data-line={i + 1}
                className={`grid grid-cols-[auto_1fr] gap-4 px-4 hover:bg-surface-raised${
                  i + 1 === highlightLine ? ' bg-accent-500/15' : ''}`}>
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

/** Valeur débouncée : renvoie `value` seulement après `ms` sans changement (évite une requête serveur par
 *  touche dans la palette de recherche). Un changement plus rapide annule le timer précédent. */
function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return debounced
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
function filePermalink(project: string, pinnedRef: string, file: string, line?: number): string {
  const dir = file.includes('/') ? file.slice(0, file.lastIndexOf('/')) : ''
  const u = new URL(`${window.location.origin}/git`)
  u.searchParams.set('project', project)
  u.searchParams.set('view', 'fichiers')
  u.searchParams.set('ref', pinnedRef)
  if (dir) u.searchParams.set('path', dir)
  u.searchParams.set('file', file)
  if (line != null) u.searchParams.set('line', String(line))   // épingle aussi la ligne ciblée (permalink)
  return u.toString()
}

/** Taille lisible : octets sous 1 Ko, sinon Ko/Mo à une décimale. */
function fmtSize(n: number): string {
  if (n < 1024) return `${n} o`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} Ko`
  return `${(n / (1024 * 1024)).toFixed(1)} Mo`
}
