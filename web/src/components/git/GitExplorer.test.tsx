import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// État injectable (hoisté pour les factories vi.mock). On isole les vraies requêtes ET les sous-composants
// lourds (RepoExplorer / GitIntelligence / ProjectCredentialCard → stubs) : ici on teste la logique PROPRE de
// GitExplorer — sélecteur de projet, vide honnête sans projet, deep-links `?view=`/`?sha=`, gating du bouton
// de réconciliation — pas le réseau ni le rendu des sous-vues (couverts par leurs propres tests).
const h = vi.hoisted(() => ({
  search: {} as { project?: string; view?: string; sha?: string },
  projects: [] as Array<{ id: string; slug: string }>,
  git: null as unknown,
  sync: undefined as unknown,
  nav: [] as Array<{ to: string; search: unknown }>,
}))

vi.mock('@/lib/queries', () => ({
  useProjects: () => ({ data: h.projects, isPending: false, isError: false, error: null }),
  useGit: () => ({
    data: h.git ?? undefined, isLoading: false, isError: false, error: null,
    refetch: () => {}, isFetching: false,
  }),
  useGitSync: () => ({ data: h.sync ?? undefined, isFetching: false, refetch: () => {} }),
  useReconcileSync: () => ({ mutate: () => {}, isPending: false, isError: false, error: null, data: undefined }),
}))

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => h.search,
  useNavigate: () => (args: { to: string; search?: (p: object) => object }) => {
    h.nav.push({ to: args.to, search: typeof args.search === 'function' ? args.search(h.search) : args.search })
  },
}))

// Sous-composants lourds neutralisés en stubs (leurs hooks/rendu ont leurs propres tests).
vi.mock('@/components/git/RepoExplorer', () => ({ RepoExplorer: () => <div>repo-explorer-stub</div> }))
vi.mock('@/components/git/GitIntelligence', () => ({
  CommitDetailCard: ({ sha }: { sha: string }) => <div>commit-detail-{sha}</div>,
  DiffCard: () => <div>diff-card-stub</div>,
}))
vi.mock('@/components/credential/ProjectCredentialCard', () => ({
  ProjectCredentialCard: () => <div>credential-stub</div>,
}))

const { GitExplorer } = await import('./GitExplorer')

const GIT_VIEW = {
  project: 'alpha',
  // Sujet de branche ≠ sujet du commit de log → cliquer le second ne heurte pas le premier dans le test.
  branches: [{ name: 'dev', sha: 'abc1234', subject: 'tip de dev' }],
  tags: [],
  ahead_behind: null,
  logs: { dev: [{ sha: 'abc1234', subject: 'feat: x' }] },
}

// Un état de sync miroir RÉCONCILIABLE (SoT en retard) → needsReconcile(data) === true.
const SYNC_RECONCILABLE = {
  project: 'alpha', remote: 'origin', fetched: true,
  branches: { dev: { ahead: 0, behind: 2, state: 'remote_ahead' } },
  state: 'remote_ahead',
}

describe('GitExplorer', () => {
  beforeEach(() => {
    h.search = {}
    h.projects = []
    h.git = null
    h.sync = undefined
    h.nav = []
    try { localStorage.clear() } catch { /* jsdom : rien */ }
  })

  it('sans projet sélectionné : rend le sélecteur + un vide honnête, et navigue au choix', () => {
    h.projects = [{ id: '1', slug: 'alpha' }]
    render(<GitExplorer />)
    // sélecteur de projet (git est per-projet) + invite explicite
    expect(screen.getByLabelText('Projet')).toBeInTheDocument()
    expect(screen.getByText('Choisis un projet')).toBeInTheDocument()
    // choisir un projet → deep-link `?project=`
    fireEvent.change(screen.getByLabelText('Projet'), { target: { value: 'alpha' } })
    expect(h.nav).toContainEqual({ to: '/git', search: { project: 'alpha' } })
  })

  it('deep-link `?project=&view=fichiers` rend la vue Fichiers (arbre)', () => {
    h.search = { project: 'alpha', view: 'fichiers' }
    h.git = GIT_VIEW
    render(<GitExplorer />)
    expect(screen.getByText('repo-explorer-stub')).toBeInTheDocument()
  })

  it('un clic sur un commit du log déplié deep-linke `?sha=`', () => {
    h.search = { project: 'alpha' }  // vue par défaut = historique
    h.git = GIT_VIEW
    render(<GitExplorer />)
    // dev (1ʳᵉ réf protégée) est déplié par défaut → son log est visible ; le sujet du commit est cliquable
    fireEvent.click(screen.getByText('feat: x'))
    expect(h.nav).toContainEqual({ to: '/git', search: { project: 'alpha', sha: 'abc1234' } })
  })

  it('accordéon : cliquer la rangée de branche replie/déplie son log inline', () => {
    h.search = { project: 'alpha' }
    h.git = GIT_VIEW
    render(<GitExplorer />)
    // dev est déplié par défaut → le commit du log est présent…
    expect(screen.getByText('feat: x')).toBeInTheDocument()
    // …la rangée dépliée est le bouton `aria-expanded` ; le cliquer la replie → le log disparaît.
    fireEvent.click(screen.getByRole('button', { expanded: true }))
    expect(screen.queryByText('feat: x')).toBeNull()
  })

  it('le bouton « Réconcilier » reste caché sans divergence, et apparaît quand needsReconcile', () => {
    h.search = { project: 'alpha' }
    h.git = GIT_VIEW
    // sync absent → aucune réconciliation proposée
    h.sync = undefined
    const { rerender } = render(<GitExplorer />)
    expect(screen.queryByText(/Réconcilier/)).toBeNull()
    // sync réconciliable → le bouton s'expose (consent-gated : il ne fait qu'ouvrir le panneau)
    h.sync = SYNC_RECONCILABLE
    rerender(<GitExplorer />)
    expect(screen.getByText(/Réconcilier/)).toBeInTheDocument()
  })
})
