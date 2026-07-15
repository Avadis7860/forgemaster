import { useState, type FormEvent } from 'react'
import { Alert, Button, Input } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useSetMirror } from '@/lib/queries'

/** Affordance « rendre GitHub-backed » : configure / édite / retire l'URL du **miroir GitHub** d'un projet.
 *  Un miroir posé rend un token de push *requis* (l'affordance credential apparaît alors). Partagée entre
 *  le panneau Réglages et la vue projet — une seule affordance, deux emplacements. */
export function MirrorForm({ project, mirror }: { project: string; mirror: string | null }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState(mirror ?? '')
  const set = useSetMirror(project)

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    set.mutate(url.trim() || null, { onSuccess: () => setOpen(false) })
  }

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {mirror ? (
          <code className="max-w-xs truncate font-mono text-xs text-muted" title={mirror}>
            {mirror}
          </code>
        ) : (
          <span className="text-xs text-faint">aucun miroir</span>
        )}
        {/* Action constructive → `secondary` (bordée, visible) : elle ne doit pas être moins affordante que
            l'action destructive `Délier` voisine (axe 6 : un élément cliquable doit paraître cliquable). */}
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            setUrl(mirror ?? '')
            setOpen(true)
          }}
        >
          {mirror ? 'Éditer' : 'Configurer le miroir'}
        </Button>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className="w-full max-w-sm space-y-2">
      <Input
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://github.com/moi/repo.git"
        aria-label="URL du miroir GitHub"
        autoComplete="off"
      />
      {set.isError && (
        <Alert tone="danger">
          {set.error instanceof ApiError ? set.error.detail : 'Échec de l’enregistrement.'}
        </Alert>
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" size="sm" busy={set.isPending}>
          Enregistrer
        </Button>
        {mirror && (
          <Button
            type="button"
            variant="danger"
            size="sm"
            busy={set.isPending}
            onClick={() => set.mutate(null, { onSuccess: () => setOpen(false) })}
          >
            Retirer
          </Button>
        )}
        <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Annuler
        </Button>
      </div>
      <p className="text-xs text-faint">
        Poussé best-effort au writeback (le SoT local reste la vérité). Un miroir configuré rend un token de
        push requis.
      </p>
    </form>
  )
}
