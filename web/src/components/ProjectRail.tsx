import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useCreateProject, useProjects } from '@/lib/queries'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Alert, Badge, Button, Card, Eyebrow, EmptyState, Input, LoadingState } from '@/components/ui'

/** Rail de gauche = la vue « Projets » : liste sélectionnable + création. Contexte global du shell. */
export function ProjectRail() {
  const projects = useProjects()
  const active = useParams({ strict: false }).project
  const navigate = useNavigate()
  const create = useCreateProject()
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')

  function onCreate(e: FormEvent) {
    e.preventDefault()
    const trimmed = slug.trim()
    if (!trimmed) return
    create.mutate(
      { slug: trimmed, name: name.trim() || null },
      {
        onSuccess: (project) => {
          setSlug('')
          setName('')
          navigate({ to: '/$project', params: { project: project.slug } })
        },
      },
    )
  }

  return (
    <aside className="z-(--z-rail) flex w-72 shrink-0 flex-col border-r border-border bg-surface/40">
      <div className="border-b border-border px-4 py-3">
        <Eyebrow>Projets</Eyebrow>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {projects.isPending ? (
          <LoadingState label="Chargement des projets…" />
        ) : projects.isError ? (
          <Alert tone="danger" title="Daemon injoignable">
            Vérifie que <code>cockpit serve</code> tourne.
          </Alert>
        ) : projects.data.length === 0 ? (
          <EmptyState title="Aucun projet" description="Crée le premier ci-dessous." />
        ) : (
          <ul className="space-y-1.5">
            {projects.data.map((p) => {
              const isActive = p.slug === active
              return (
                <Card
                  as="li"
                  key={p.id}
                  className={cn('transition-colors', isActive && 'border-accent-500/50 bg-surface-raised')}
                >
                  <Link
                    to="/$project"
                    params={{ project: p.slug }}
                    className="block rounded-card px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-fg">{p.slug}</span>
                      <Badge tone={isActive ? 'accent' : 'neutral'}>{p.backend}</Badge>
                    </div>
                    {p.name && <p className="truncate text-xs text-muted">{p.name}</p>}
                  </Link>
                </Card>
              )
            })}
          </ul>
        )}
      </div>

      <form onSubmit={onCreate} className="space-y-2 border-t border-border p-3">
        <Eyebrow>Nouveau projet</Eyebrow>
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
        {create.isError && (
          <Alert tone="danger">
            {create.error instanceof ApiError ? create.error.detail : 'Échec de la création.'}
          </Alert>
        )}
        <Button type="submit" variant="primary" busy={create.isPending} disabled={!slug.trim()} className="w-full">
          Créer le projet
        </Button>
      </form>
    </aside>
  )
}
