import { useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import {
  Alert, Badge, Button, Card, EmptyState, LoadingState, SectionTitle, Select,
} from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useProjects, useTemplates } from '@/lib/queries'
import type { TemplateSummary } from '@/lib/schemas'

type Search = { tpl?: string }

/** L'asset statique d'un template : préview/entrée sont des chemins RELATIFS au dossier, servis par le daemon
 *  à `/templates/<slug>/…` (jamais via l'API). Un seul point de vérité pour construire l'URL. */
const asset = (slug: string, rel: string) => `/templates/${encodeURIComponent(slug)}/${rel}`

/** Vitrine READ-ONLY des **templates de référence UI** — le capital-token *montrable* (le pendant visuel du
 *  blueprint) : des modèles d'UI qu'une session peut appliquer à un projet. Frère de `BundleExplorer` (ce que
 *  le cockpit sème) et `CapitalExplorer` (ce que le MCP loue), mais avec **une action** : matérialiser
 *  l'intention « applique le template X à mon projet Y » (l'application réelle est déléguée en aval).
 *
 *  Piloté par l'URL (`?tpl=<slug>`) → deep-linkable, capturable at-rest. Grille de cards (vignette `preview.png`)
 *  → fiche (iframe LIVE du template, colorimétrie switchable) + bloc action (prompt copiable). Aucune mutation. */
export function TemplateExplorer() {
  const templates = useTemplates()
  const search = useSearch({ strict: false }) as Search
  const navigate = useNavigate()

  const open = (slug: string | undefined) =>
    navigate({ to: '/templates', search: () => (slug ? { tpl: slug } : {}) })

  if (templates.isLoading) {
    return <VitrineShell><VitrineHeader /><LoadingState label="Lecture des templates servis…" /></VitrineShell>
  }
  if (templates.isError || !templates.data) {
    return (
      <VitrineShell>
        <VitrineHeader />
        <Alert tone="danger" title="Vitrine indisponible">
          {templates.error instanceof ApiError ? templates.error.detail : String(templates.error)}
        </Alert>
      </VitrineShell>
    )
  }

  const list = templates.data
  const selected = search.tpl ? list.find((t) => t.slug === search.tpl) : undefined

  // Sur une fiche deep-linkée (`?tpl=`), l'entrée doit être LA FICHE (son sujet), pas l'en-tête vitrine
  // générique : la fiche porte son propre chapeau (fil d'Ariane retour + nom du template promu en titre) et
  // le `VitrineHeader` s'efface. Les autres états (grille/vide/introuvable) gardent le chapeau vitrine.
  if (selected) {
    return (
      <VitrineShell>
        <TemplateDetail template={selected} onBack={() => open(undefined)} />
      </VitrineShell>
    )
  }

  return (
    <VitrineShell>
      <VitrineHeader />
      {list.length === 0 ? (
        <EmptyState
          title="Aucun template de référence"
          description="Aucun modèle d'UI n'est servi par cette install. Ajoute un dossier sous web/public/templates/ (voir le README du format), puis rebuild."
        />
      ) : search.tpl ? (
        <EmptyState
          title="Template introuvable"
          description={`Aucun template « ${search.tpl} » dans la vitrine.`}
        />
      ) : (
        <TemplateGrid list={list} onOpen={(t) => open(t.slug)} />
      )}
    </VitrineShell>
  )
}

/** Le cadre commun des 3 explorers de corpus (carte `space-y-4 p-5`) — cohérence avec Bundle/CapitalExplorer.
 *  Ne porte QUE le shell ; le chapeau (vitrine ou fiche) est rendu par l'appelant selon l'état. */
function VitrineShell({ children }: { children: React.ReactNode }) {
  return <Card className="space-y-4 p-5">{children}</Card>
}

/** Le chapeau de la vitrine (grille/vide) : eyebrow + titre + intention de la surface. Absent sur une fiche
 *  (où le sujet — le template — prend la tête). */
function VitrineHeader() {
  return (
    <div className="space-y-4">
      <SectionTitle
        eyebrow="Capital-token montrable"
        title="Vitrine des templates"
        actions={<Badge tone="neutral" className="whitespace-nowrap">lecture seule</Badge>}
      />
      <p className="-mt-2 text-sm text-muted">
        Des modèles d'UI beaux et concrets — le pendant visuel du blueprint. Parcours-les, puis matérialise
        l'intention « applique le template X à mon projet Y » (l'application est menée par une session Claude).
      </p>
    </div>
  )
}

// -- grille ------------------------------------------------------------------------------------------

/** La grille de cards : par template, sa vignette `preview.png` (statique, at-rest), son nom, son
 *  archetype/sous-genre en badges, et son intention. Clic → fiche (deep-link `?tpl=`). */
function TemplateGrid({ list, onOpen }: { list: TemplateSummary[]; onOpen: (t: TemplateSummary) => void }) {
  return (
    <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {list.map((t) => (
        <li key={t.slug}>
          <button
            type="button"
            onClick={() => onOpen(t)}
            className="group flex w-full flex-col overflow-hidden rounded-card border border-border bg-surface text-left transition-colors hover:border-accent-500 focus-visible:border-accent-500 focus-visible:outline-none"
            aria-label={`Ouvrir le template ${t.name}`}
          >
            {/* Vignette 16:10 : la preview statique remplit le cadre (relief par l'image, pas un panneau). */}
            <span className="block aspect-[16/10] overflow-hidden border-b border-border bg-surface-raised">
              <img
                src={asset(t.slug, t.preview)}
                alt={`Aperçu du template ${t.name}`}
                loading="lazy"
                className="size-full object-cover transition-transform duration-200 group-hover:scale-[1.02]"
              />
            </span>
            <span className="flex min-w-0 flex-col gap-1.5 p-3">
              <span className="truncate text-sm font-medium text-fg">{t.name}</span>
              <span className="flex flex-wrap gap-1">
                {t.tool_type && <Badge tone="accent">{t.tool_type}</Badge>}
                {t.genre && <Badge tone="neutral">{t.genre}</Badge>}
              </span>
              {t.intention && <span className="line-clamp-2 text-xs text-muted">{t.intention}</span>}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

// -- fiche -------------------------------------------------------------------------------------------

/** La fiche d'un template — l'ENTRÉE de la vue quand `?tpl=` est présent : le sujet (le template) prend la
 *  tête. Fil d'Ariane retour (vrai lien) → nom promu en `SectionTitle` + badges, l'**iframe live** (le
 *  template rendu, colorimétrie switchable par son sélecteur inline), puis le bloc action (prompt copiable). */
function TemplateDetail({ template, onBack }: { template: TemplateSummary; onBack: () => void }) {
  return (
    <div className="space-y-4">
      {/* Fil d'Ariane : lien retour explicite (affordance de lien, pas un ghost muted noyé) — la vitrine
          générique se replie en un pas de navigation, elle ne coiffe plus le sujet. */}
      <button
        type="button"
        onClick={onBack}
        className="-ml-1 inline-flex items-center gap-1 rounded-card px-1 py-0.5 text-xs font-medium text-muted transition-colors hover:text-accent-500 focus-visible:text-accent-500 focus-visible:outline-none"
      >
        <span aria-hidden>←</span> Vitrine des templates
      </button>

      <div className="space-y-1.5">
        <SectionTitle
          eyebrow="Template de référence"
          title={template.name}
          actions={<Badge tone="neutral" className="whitespace-nowrap">lecture seule</Badge>}
        />
        <div className="flex flex-wrap gap-1">
          {template.tool_type && <Badge tone="accent">{template.tool_type}</Badge>}
          {template.genre && <Badge tone="neutral">{template.genre}</Badge>}
          {template.themes.length > 0 && (
            <Badge tone="neutral">{template.themes.length} colorimétries</Badge>
          )}
        </div>
      </div>

      {template.intention && <p className="text-sm text-muted">{template.intention}</p>}

      {/* Aperçu LIVE : le template servi at-rest, dans un cadre 16:9. `sandbox=allow-scripts` isole le
          document tout en laissant tourner son switch de colorimétrie inline (aucun same-origin requis). */}
      <div className="overflow-hidden rounded-card border border-border bg-surface-raised">
        <iframe
          src={asset(template.slug, template.entry)}
          title={`Aperçu live du template ${template.name}`}
          loading="lazy"
          sandbox="allow-scripts"
          className="block aspect-video w-full"
        />
      </div>

      <ApplyBlock template={template} />
    </div>
  )
}

/** Le bloc action « appliquer » : sélecteur de projet + prompt **traçable** copiable. **Aucune mutation** —
 *  la fiche matérialise l'intention (« applique le template X à mon projet Y ») ; l'application réelle est
 *  menée par une session Claude en aval. C'est l'unique action de la vitrine (goto-safe, read-only). */
function ApplyBlock({ template }: { template: TemplateSummary }) {
  const projects = useProjects()
  const [project, setProject] = useState('')
  const [copied, setCopied] = useState(false)

  const target = project || '<projet>'
  const prompt = `applique le template ${template.slug} à mon projet ${target}`

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(prompt)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      /* clipboard indisponible (contexte non sécurisé) — le prompt reste sélectionnable à la main */
    }
  }

  const options = projects.data ?? []

  return (
    <div className="space-y-3 rounded-card border border-border bg-surface-raised p-4">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-medium text-fg">Appliquer à un projet</h4>
        <Badge tone="neutral" className="whitespace-nowrap">intention · sans effet</Badge>
      </div>

      {projects.isError ? (
        <Alert tone="danger" title="Projets indisponibles">
          {projects.error instanceof ApiError ? projects.error.detail : String(projects.error)}
        </Alert>
      ) : options.length === 0 && !projects.isLoading ? (
        <p className="text-sm text-muted">Aucun projet — crée-en un d'abord, puis reviens choisir un template.</p>
      ) : (
        <Select
          aria-label="Projet cible"
          value={project}
          onChange={(e) => setProject(e.target.value)}
          disabled={projects.isLoading}
        >
          <option value="">— choisir un projet —</option>
          {options.map((p) => (
            <option key={p.id} value={p.slug}>{p.slug}</option>
          ))}
        </Select>
      )}

      {/* Le prompt traçable, en lecture (sélectionnable) + copie un clic. Une seule action primaire (axe 2). */}
      <div className="flex items-stretch gap-2">
        <code className="min-w-0 flex-1 truncate rounded-card border border-border bg-surface px-3 py-2 font-mono text-xs text-fg"
          title={prompt}>
          {prompt}
        </code>
        <Button variant="primary" size="sm" onClick={copy} className="shrink-0">
          {copied ? 'Copié ✓' : 'Copier'}
        </Button>
      </div>
    </div>
  )
}
