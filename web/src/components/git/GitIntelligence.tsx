import { useState } from 'react'
import { Alert, Badge, Button, Card, EmptyState, LoadingState } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useGitCommit, useGitDiff } from '@/lib/queries'
import type { GitBranch, GitCommitFile } from '@/lib/schemas'

/** Détail d'un commit (read-only) : métadonnées + fichiers touchés avec `+/-` par fichier. Ouvert par
 *  clic sur une entrée de log/branche (state React → inatteignable au runner goto-only, couvert en vitest).
 *  Un seul GET idempotent (`useGitCommit`). `onClose` referme le panneau (revient à la vue synchro). */
export function CommitDetailCard({ project, sha, onClose }: {
  project: string
  sha: string
  onClose: () => void
}) {
  const { data, isLoading, isError, error } = useGitCommit(project, sha)

  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-fg">Détail du commit</p>
        <Button variant="ghost" size="sm" onClick={onClose}>Fermer</Button>
      </div>
      {isLoading && <LoadingState label="Lecture du commit…" />}
      {(isError || (!isLoading && !data)) && (
        <Alert tone="danger" title="Commit indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      )}
      {data && (
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="text-sm font-medium text-fg">{data.subject}</p>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <code className="font-mono text-faint">{data.short}</code>
              <span>{data.author}</span>
              <span className="text-faint">·</span>
              <span>{fmtDate(data.date)}</span>
            </div>
          </div>
          {data.body && (
            <pre className="overflow-auto whitespace-pre-wrap rounded-card bg-surface-raised p-3 text-xs
              leading-relaxed text-muted">{data.body}</pre>
          )}
          <div className="space-y-1">
            <p className="text-xs text-faint">{data.files.length} fichier(s) touché(s)</p>
            <ul className="space-y-0.5">
              {data.files.map((f) => <CommitFileRow key={f.path} file={f} />)}
            </ul>
          </div>
        </div>
      )}
    </Card>
  )
}

/** Une ligne de fichier touché : chemin mono + `+adds / -dels` (ou drapeau binaire). */
function CommitFileRow({ file }: { file: GitCommitFile }) {
  return (
    <li className="flex items-center gap-3 text-xs">
      <code className="min-w-0 flex-1 truncate font-mono text-fg" title={file.path}>{file.path}</code>
      {file.binary ? (
        <Badge tone="neutral">binaire</Badge>
      ) : (
        <span className="flex shrink-0 gap-2 font-mono">
          <span className="text-ok-500">+{file.additions ?? 0}</span>
          <span className="text-danger-500">−{file.deletions ?? 0}</span>
        </span>
      )}
    </li>
  )
}

/** Diff d'une feature (`base...head`, three-dot = depuis la merge-base) rendu unifié. Deux sélecteurs de réf
 *  (base + head) alimentés par les branches déjà chargées ; défaut base=`main`, head=`dev` (le retard que
 *  main doit rattraper). Un seul GET idempotent (`useGitDiff`) — goto-only safe. */
export function DiffCard({ project, branches }: { project: string; branches: GitBranch[] }) {
  const refs = branches.map((b) => b.name)
  const [base, setBase] = useState(refs.includes('main') ? 'main' : (refs[0] ?? 'main'))
  const [head, setHead] = useState(refs.includes('dev') ? 'dev' : (refs[refs.length - 1] ?? 'dev'))
  const { data, isLoading, isError, error } = useGitDiff(project, base, head)

  return (
    <Card className="space-y-3 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-medium text-fg">Diff de feature</p>
        <div className="flex items-center gap-2 text-sm text-muted">
          <RefSelect label="base" value={base} refs={refs} onChange={setBase} />
          <span className="text-faint">…</span>
          <RefSelect label="head" value={head} refs={refs} onChange={setHead} />
        </div>
      </div>
      {isLoading && <LoadingState label="Calcul du diff…" />}
      {(isError || (!isLoading && !data)) && (
        <Alert tone="danger" title="Diff indisponible">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      )}
      {data && (data.diff === '' ? (
        <EmptyState title="Aucune différence"
          description={`${base} et ${head} sont alignés depuis leur merge-base — rien à comparer.`} />
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-faint">{data.files.length} fichier(s) changé(s)</p>
          <DiffBody diff={data.diff} />
        </div>
      ))}
    </Card>
  )
}

/** Un sélecteur de réf (branche) réutilisant les branches chargées — aucune requête de plus. */
function RefSelect({ label, value, refs, onChange }: {
  label: string
  value: string
  refs: string[]
  onChange: (v: string) => void
}) {
  return (
    <label className="flex items-center gap-1.5">
      <span className="text-faint">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 rounded-card border border-border bg-surface-raised px-2 text-sm text-fg"
      >
        {refs.map((r) => <option key={r} value={r}>{r}</option>)}
      </select>
    </label>
  )
}

/** Corps du diff unifié : chaque ligne colorée par son préfixe (ajout ok / retrait danger / hunk accent /
 *  en-tête de fichier fg). Rendu at-rest par HTTP → capturable par la boucle visuelle. */
function DiffBody({ diff }: { diff: string }) {
  const lines = diff.split('\n')
  return (
    <div className="overflow-auto rounded-card border border-border">
      <pre className="min-w-full text-xs leading-relaxed">
        <code className="grid font-mono">
          {lines.map((line, i) => (
            <span key={i} className={`whitespace-pre px-4 ${diffLineClass(line)}`}>{line || ' '}</span>
          ))}
        </code>
      </pre>
    </div>
  )
}

/** Classe d'une ligne de diff selon son préfixe. Tokens sémantiques (jamais de teinte inline R5). */
function diffLineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'text-muted'
  if (line.startsWith('diff ') || line.startsWith('index ')) return 'text-faint'
  if (line.startsWith('@@')) return 'text-accent-400'
  if (line.startsWith('+')) return 'bg-ok-500/10 text-ok-500'
  if (line.startsWith('-')) return 'bg-danger-500/10 text-danger-500'
  return 'text-fg'
}

/** Date ISO → libellé court local (jamais « hier ») ; repli sur la valeur brute si non parsable. */
function fmtDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}
