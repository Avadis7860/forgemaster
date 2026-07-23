import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TemplateSummary } from '@/lib/schemas'

// État injectable (hoisté pour les factories vi.mock). On isole les vraies requêtes : ici on teste la
// NAVIGATION grille → fiche (deep-link `?tpl=`) et le bloc action (prompt traçable), pas le réseau.
const h = vi.hoisted(() => ({
  templates: [] as TemplateSummary[],
  tpl: undefined as string | undefined,
  projects: [] as Array<{ id: string; slug: string }>,
  nav: [] as Array<{ to: string; search: unknown }>,
  inspire: {
    mutate: vi.fn(), reset: vi.fn(),
    isPending: false, isError: false, error: null as unknown, data: null as unknown,
  },
}))

vi.mock('@/lib/queries', () => ({
  useTemplates: () => ({ data: h.templates, isLoading: false, isError: false, error: null }),
  useProjects: () => ({ data: h.projects, isLoading: false, isError: false, error: null }),
  useInspireProject: () => h.inspire,
}))

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => ({ tpl: h.tpl }),
  useNavigate: () => (args: { to: string; search: (p: object) => object }) => {
    // Résout le `search` (fonction) comme le fait le routeur, pour capturer la destination réelle.
    h.nav.push({ to: args.to, search: args.search({}) })
  },
}))

const { TemplateExplorer } = await import('./TemplateExplorer')

const T: TemplateSummary = {
  slug: 'browser-game-spatial',
  name: 'Browser-game spatial — deep-space',
  tool_type: 'browser-game',
  genre: 'spatial',
  tags: ['browser-game', 'spatial'],
  intention: 'Écran de commandement deep-space.',
  preview: 'preview.png',
  entry: 'index.html',
  themes: ['orbital', 'reactor', 'void'],
}

describe('TemplateExplorer', () => {
  beforeEach(() => {
    h.templates = []
    h.tpl = undefined
    h.projects = []
    h.nav = []
    h.inspire = {
      mutate: vi.fn(), reset: vi.fn(),
      isPending: false, isError: false, error: null, data: null,
    }
  })

  it('rend la grille de cards et deep-linke vers la fiche au clic', () => {
    h.templates = [T]
    render(<TemplateExplorer />)
    // card : nom + badges archetype/genre + intention
    expect(screen.getByText(T.name)).toBeInTheDocument()
    expect(screen.getByText('browser-game')).toBeInTheDocument()
    expect(screen.getByText('spatial')).toBeInTheDocument()
    expect(screen.getByText('Écran de commandement deep-space.')).toBeInTheDocument()
    // clic → navigation vers ?tpl=<slug>
    fireEvent.click(screen.getByLabelText(`Ouvrir le template ${T.name}`))
    expect(h.nav).toEqual([{ to: '/templates', search: { tpl: T.slug } }])
  })

  it('rend la fiche (iframe live + action inspirer) quand ?tpl= pointe un template valide', () => {
    h.templates = [T]
    h.tpl = T.slug
    h.projects = [{ id: '1', slug: 'mon-projet' }]
    render(<TemplateExplorer />)
    // iframe live du template servi
    const frame = screen.getByTitle(`Aperçu live du template ${T.name}`)
    expect(frame).toHaveAttribute('src', `/templates/${T.slug}/index.html`)
    // l'action réelle : le bouton d'application (désactivé tant qu'aucun projet n'est choisi)
    const btn = screen.getByRole('button', { name: 'Inspirer ce projet' })
    expect(btn).toBeInTheDocument()
    expect(btn).toBeDisabled()
  })

  it('applique le template au projet choisi (POST /inspire) au clic', () => {
    h.templates = [T]
    h.tpl = T.slug
    h.projects = [{ id: '1', slug: 'mon-projet' }]
    render(<TemplateExplorer />)
    fireEvent.change(screen.getByLabelText('Projet cible'), { target: { value: 'mon-projet' } })
    const btn = screen.getByRole('button', { name: 'Inspirer ce projet' })
    expect(btn).toBeEnabled()                                   // projet choisi → action débloquée
    fireEvent.click(btn)
    expect(h.inspire.mutate).toHaveBeenCalledWith(T.slug)       // applique CE template
  })

  it('affiche le compte-rendu de succès après application', () => {
    h.templates = [T]
    h.tpl = T.slug
    h.projects = [{ id: '1', slug: 'mon-projet' }]
    h.inspire.data = {
      project: 'mon-projet', template: T.slug, feature: 'design-browser-game-spatial',
      task: 'customize-ui', files: ['brief.md', 'tokens.css', 'preview.png'],
    }
    render(<TemplateExplorer />)
    expect(screen.getByText('Template appliqué à mon-projet')).toBeInTheDocument()
    expect(screen.getByText('design-browser-game-spatial')).toBeInTheDocument()
  })

  it('affiche un vide honnête quand aucun template n\'est servi', () => {
    h.templates = []
    render(<TemplateExplorer />)
    expect(screen.getByText('Aucun template de référence')).toBeInTheDocument()
  })

  it('signale un slug introuvable sans planter', () => {
    h.templates = [T]
    h.tpl = 'inexistant'
    render(<TemplateExplorer />)
    expect(screen.getByText('Template introuvable')).toBeInTheDocument()
  })
})
