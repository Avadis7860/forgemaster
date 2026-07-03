import { useState, type FormEvent } from 'react'
import { useCreateProject } from '@/lib/queries'
import { ApiError } from '@/lib/api'
import { Alert, Button, Eyebrow, Input } from '@/components/ui'
import type { Project } from '@/lib/schemas'

/** Formulaire de création de projet — **source unique** partagée par le rail (nav rapide) et le wizard
 *  1er-démarrage (`/setup`). slug + nom + miroir GitHub optionnels ; `onCreated` laisse l'appelant décider
 *  de la suite (naviguer vers le projet, ou avancer d'une étape). `title` masquable pour l'usage inline. */
export function NewProjectForm(
  { onCreated, title = 'Nouveau projet', submitLabel = 'Créer le projet' }:
  { onCreated?: (project: Project) => void; title?: string | null; submitLabel?: string },
) {
  const create = useCreateProject()
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [mirror, setMirror] = useState('')

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = slug.trim()
    if (!trimmed) return
    create.mutate(
      { slug: trimmed, name: name.trim() || null, mirror_remote: mirror.trim() || null },
      {
        onSuccess: (project) => {
          setSlug('')
          setName('')
          setMirror('')
          onCreated?.(project)
        },
      },
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-2">
      {title && <Eyebrow>{title}</Eyebrow>}
      <Input
        value={slug}
        onChange={(e) => setSlug(e.target.value)}
        placeholder="slug (kebab-case)"
        aria-label="slug du projet"
      />
      <Input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="nom (optionnel)"
        aria-label="nom du projet"
      />
      <Input
        value={mirror}
        onChange={(e) => setMirror(e.target.value)}
        placeholder="miroir GitHub (optionnel)"
        aria-label="miroir GitHub du projet"
      />
      {create.isError && (
        <Alert tone="danger">
          {create.error instanceof ApiError ? create.error.detail : 'Échec de la création.'}
        </Alert>
      )}
      <Button type="submit" variant="primary" busy={create.isPending} disabled={!slug.trim()} className="w-full">
        {submitLabel}
      </Button>
    </form>
  )
}
