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
}))

vi.mock('@/lib/queries', () => ({
  useTemplates: () => ({ data: h.templates, isLoading: false, isError: false, error: null }),
  useProjects: () => ({ data: h.projects, isLoading: false, isError: false, error: null }),
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

  it('rend la fiche (iframe live + prompt) quand ?tpl= pointe un template valide', () => {
    h.templates = [T]
    h.tpl = T.slug
    h.projects = [{ id: '1', slug: 'mon-projet' }]
    render(<TemplateExplorer />)
    // iframe live du template servi
    const frame = screen.getByTitle(`Aperçu live du template ${T.name}`)
    expect(frame).toHaveAttribute('src', `/templates/${T.slug}/index.html`)
    // prompt traçable par défaut (aucun projet choisi → placeholder)
    expect(screen.getByText(`applique le template ${T.slug} à mon projet <projet>`)).toBeInTheDocument()
  })

  it('injecte le projet choisi dans le prompt copiable', () => {
    h.templates = [T]
    h.tpl = T.slug
    h.projects = [{ id: '1', slug: 'mon-projet' }]
    render(<TemplateExplorer />)
    fireEvent.change(screen.getByLabelText('Projet cible'), { target: { value: 'mon-projet' } })
    expect(screen.getByText(`applique le template ${T.slug} à mon projet mon-projet`)).toBeInTheDocument()
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
