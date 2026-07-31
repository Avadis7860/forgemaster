import { useState, type FormEvent } from 'react'
import { Alert, Button, Card, Eyebrow, FileDrop, Input } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useUploadFile } from '@/lib/queries'

// Allow-list serveur (upload.py `_ALLOWED_EXTENSIONS`) — pré-filtre le sélecteur natif (le serveur reste la
// vérité : un type hors liste → 415, montré tel quel).
const ACCEPT = '.png,.jpg,.jpeg,.svg,.webp,.md,.txt,.css,.pdf'

/** Panneau d'action « Ajouter un fichier » d'un projet : dépose un asset (charte, schéma, image, copy, doc)
 *  sous `docs/design/<dest>/` — lisible par le worker (ou l'IA d'interview, **en direct** dans son worktree).
 *  La livraison worktree-aware (live vs forge) est décidée côté serveur : ce panneau délègue et ne route pas.
 *  Un seul point d'entrée couvre les deux moments (interview ET projet en cours). Jamais de secret ici. */
export function AssetUploadCard({ project }: { project: string }) {
  const [file, setFile] = useState<File | null>(null)
  const [dest, setDest] = useState('brand')
  const upload = useUploadFile(project)
  const result = upload.data

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    upload.mutate({ file, dest: dest.trim() || undefined }, { onSuccess: () => setFile(null) })
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="space-y-1">
        <Eyebrow>Contenu · {project}</Eyebrow>
        <p className="text-sm font-medium text-fg">Ajouter un fichier</p>
        <p className="text-xs text-muted">
          Charte, schéma, image de référence, copy… déposé sous{' '}
          <code className="text-xs">docs/design/{dest.trim() || 'brand'}/</code> et lisible par le worker (ou
          l'IA d'interview, en direct). Jamais de secret ici — les tokens passent par le coffre.
        </p>
      </div>
      <form onSubmit={onSubmit} className="space-y-3">
        <FileDrop
          file={file}
          onFile={setFile}
          accept={ACCEPT}
          hint="images · .md · .txt · .css · .pdf — 10 Mo max"
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="shrink-0 text-xs uppercase tracking-wide text-faint">Dossier</span>
          <Input
            value={dest}
            onChange={(e) => setDest(e.target.value)}
            placeholder="brand · schemas · copy…"
            aria-label="sous-dossier sous docs/design/ (ex. brand, schemas, copy)"
            className="max-w-[12rem]"
          />
          {/* `secondary` (pas `primary`) : action de carte, subordonnée au CTA accent de la page — évite un
              2ᵉ accent cuivre concurrent une fois un fichier choisi. La drop-zone reste l'affordance dominante. */}
          <Button type="submit" variant="secondary" size="sm" busy={upload.isPending} disabled={!file}>
            Déposer
          </Button>
        </div>
        {upload.isError && (
          <Alert tone="danger">
            {upload.error instanceof ApiError ? upload.error.detail : 'Échec du dépôt.'}
          </Alert>
        )}
        {result && result.mode !== 'noop' && (
          <Alert tone="ok">
            Déposé dans <code className="text-xs">{result.file}</code> — feature {result.feature} (
            {result.mode === 'live' ? 'worktree actif, lisible en direct' : 'voie forge, merge à valider'}).
          </Alert>
        )}
      </form>
    </Card>
  )
}
