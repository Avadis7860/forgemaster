import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useCreateProject, useProjects } from '@/lib/queries'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Alert, Badge, Button, Card, Eyebrow, EmptyState, Input, LoadingState } from '@/components/ui'
import type { Project } from '@/lib/schemas'

/** Une entité du rail (projet ou outil) : carte sélectionnable → workspace. */
function EntityCard({ p, active }: { p: Project; active: string | undefined }) {
  const isActive = p.slug === active
  return (
    <Card
      as="li"
      className={cn('transition-colors', isActive && 'border-accent-500/50 bg-surface-raised')}
    >
      <Link to="/$project" params={{ project: p.slug }} className="block rounded-card px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-fg">{p.slug}</span>
          <Badge tone={isActive ? 'accent' : 'neutral'}>{p.backend}</Badge>
        </div>
        {p.name && <p className="truncate text-xs text-muted">{p.name}</p>}
      </Link>
    </Card>
  )
}

/** Une section titrée du rail (Projets / Outils), rendue seulement si elle a des entités. */
function EntitySection({ label, items, active }: { label: string; items: Project[]; active: string | undefined }) {
  if (items.length === 0) return null
  return (
    <div className="space-y-1.5">
      <Eyebrow>{label}</Eyebrow>
      <ul className="space-y-1.5">
        {items.map((p) => <EntityCard key={p.id} p={p} active={active} />)}
      </ul>
    </div>
  )
}

/** Rail de gauche = l'espace de travail : entités classées en **Projets** (travaillés) et **Outils**
 *  (génériques du framework) via `kind`, sélectionnables + création. Contexte global du shell. */
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
        <Eyebrow>Espace de travail</Eyebrow>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
        {projects.isPending ? (
          <LoadingState label="Chargement de l’espace…" />
        ) : projects.isError ? (
          <Alert tone="danger" title="Daemon injoignable">
            Vérifie que <code>cockpit serve</code> tourne.
          </Alert>
        ) : projects.data.length === 0 ? (
          <EmptyState title="Espace vide" description="Crée le premier projet ci-dessous." />
        ) : (
          <>
            <EntitySection
              label="Projets"
              items={projects.data.filter((p) => p.kind !== 'tool')}
              active={active}
            />
            <EntitySection
              label="Outils"
              items={projects.data.filter((p) => p.kind === 'tool')}
              active={active}
            />
          </>
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
