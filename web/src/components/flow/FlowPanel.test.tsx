import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// État injectable (hoisté pour les factories vi.mock). On isole les vraies requêtes : ici on teste que
// la vue adresse une opération par son `entry` UNIQUE (pas son label) — le fix du masquage `cli:main`.
const h = vi.hoisted(() => ({
  ops: [] as Array<{ operation: string; entry: string; kind: string }>,
  op: undefined as string | undefined,
  flowCalls: [] as string[],   // les `selector` passés à useFlow, dans l'ordre
}))

vi.mock('@/lib/queries', () => ({
  useFlowOperations: () => ({
    data: { operations: h.ops }, isLoading: false, isError: false, error: null, refetch: vi.fn(), isFetching: false,
  }),
  useFlow: (_project: string, selector: string) => {
    h.flowCalls.push(selector)
    return {
      data: selector
        ? { ok: true, operation: selector, entry: selector, nodes: [], edges: [],
            stats: { nodes: 0, edges: 0, edges_indirect: 0, indirect_ratio: 0 } }
        : undefined,
      isLoading: false, isError: false, error: null, refetch: vi.fn(), isFetching: false,
    }
  },
}))

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => ({ op: h.op }),
  useNavigate: () => vi.fn(),
}))

vi.mock('@/components/flow/FlowGraph', () => ({
  FlowGraph: ({ flow }: { flow: { entry?: string } }) => <div data-testid="flow-graph">{flow?.entry}</div>,
}))

const { FlowPanel } = await import('./FlowPanel')

// Deux `cli:main` de fichiers DIFFÉRENTS (le hook triait en tête et masquait le vrai) + une route.
const OPS = [
  { operation: 'cli:main', entry: '.claude/hooks/post-edit-check.py::main', kind: 'cli' },
  { operation: 'cli:main', entry: 'src/pkg/cli.py::main', kind: 'cli' },
  { operation: 'GET /x', entry: 'src/app.py::handler', kind: 'route' },
]

describe('FlowPanel — identité d\'opération par entry', () => {
  beforeEach(() => { h.flowCalls = []; h.op = undefined; h.ops = OPS })

  it('deux `cli:main` → deux options à VALEUR distincte (l\'entry), affichage désambiguïsé par fichier', () => {
    render(<FlowPanel project="demo" />)
    const values = screen.getAllByRole('option').map((o) => (o as HTMLOptionElement).value)
    expect(values).toContain('.claude/hooks/post-edit-check.py::main')
    expect(values).toContain('src/pkg/cli.py::main')
    expect(screen.getByText(/cli:main.*src\/pkg\/cli\.py/)).toBeInTheDocument()
  })

  it('un ?op= vers le 2ᵉ `cli:main` n\'est PAS masqué (résolu par entry, pas par label)', () => {
    h.op = 'src/pkg/cli.py::main'
    render(<FlowPanel project="demo" />)
    expect(h.flowCalls.at(-1)).toBe('src/pkg/cli.py::main')        // et surtout PAS l'entry du hook
    expect(screen.getByTestId('flow-graph')).toHaveTextContent('src/pkg/cli.py::main')
  })

  it('défaut sans ?op= = la 1ʳᵉ route, adressée par son entry', () => {
    render(<FlowPanel project="demo" />)
    expect(h.flowCalls.at(-1)).toBe('src/app.py::handler')
  })
})
