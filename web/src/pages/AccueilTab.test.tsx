import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// État injectable (hoisté pour les factories vi.mock). On teste le câblage de l'Accueil : docs FONDU (rendu
// du Markdown via DocView lazy + repli honnête) et les 3 tuiles de scent avec leur badge dérivé.
const h = vi.hoisted(() => ({
  docs: undefined as unknown,
  features: [] as Array<{ next: string | null; worktree_path: string | null; tasks?: unknown[] }>,
  deployments: [] as Array<{ status: string }>,
}))

vi.mock('@/lib/queries', () => ({
  useDocs: () => ({ data: h.docs, isLoading: false, isError: false, error: null }),
  useRoadmap: () => ({ data: { features: h.features }, isLoading: false, isError: false, error: null }),
  useDeployments: () => ({ data: { deployments: h.deployments }, isLoading: false, isError: false, error: null }),
  // LaunchCycle (rendu par l'Accueil) consomme ces deux hooks ; ici les fixtures n'ont pas de socle interactif
  // (tasks vides) → LaunchCycle rend `null` et n'interfère pas avec les assertions de l'Accueil.
  useRoadmapCheck: () => ({ data: { ok: false } }),
  useReconcileSocle: () => ({ isPending: false, isError: false, error: null, data: undefined, mutate: vi.fn() }),
}))
vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ project: 'code-map' }),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}))
// react-markdown est lourd + lazy → on stube le renderer (on teste le câblage, pas le Markdown).
vi.mock('@/components/docs/DocView', () => ({
  DocView: ({ content }: { content: string }) => <div data-testid="docview">{content}</div>,
}))

const { AccueilTab } = await import('./AccueilTab')

describe('AccueilTab', () => {
  beforeEach(() => { h.docs = undefined; h.features = []; h.deployments = [] })

  it('doc présente → la rend (DocView lazy) dans la carte « ce que c\'est »', async () => {
    h.docs = { found: true, path: 'docs/tool-card.md', content: '# code-map', truncated: false }
    render(<AccueilTab />)
    expect(await screen.findByTestId('docview')).toHaveTextContent('# code-map')
  })

  it('pas de doc → repli honnête inline (pas une erreur)', () => {
    h.docs = { found: false, path: null, content: '', truncated: false }
    render(<AccueilTab />)
    expect(screen.getByText(/n'a pas de/)).toBeInTheDocument()
  })

  it('tuiles de scent : badge Roadmap dérive N features · X NEXT, Ops compte les déploiements servis', () => {
    h.docs = { found: false, path: null, content: '', truncated: false }
    h.features = [
      { next: 'p0', worktree_path: '/w/x', tasks: [] },
      { next: null, worktree_path: null, tasks: [] },
    ]
    h.deployments = [{ status: 'running' }, { status: 'no_deploy' }]
    render(<AccueilTab />)
    expect(screen.getByText(/2 features · 1 NEXT/)).toBeInTheDocument()
    expect(screen.getByText('branche à valider')).toBeInTheDocument()
    expect(screen.getByText('1 déploiement')).toBeInTheDocument()
  })
})
