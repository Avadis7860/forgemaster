import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// État injectable (hoisté pour les factories vi.mock) : on isole les vraies requêtes et on prouve le rendu
// tissu des 3 vues (tokens groupés / primitives / routes) + le switch `<Segmented/>` + les EmptyState.
const h = vi.hoisted(() => ({
  tokens: [] as Array<{ name: string; value: string; group: string; source_file: string; line: number }>,
  primitives: [] as Array<{ name: string; file: string; line: number; lead?: string }>,
  routes: [] as Array<{ var: string; path: string | null; full_path: string; component?: string; parent: string | null; is_root: boolean; file: string; line: number }>,
}))

const q = (data: unknown) => ({
  data, isLoading: false, isError: false, error: null, refetch: vi.fn(), isFetching: false,
})

vi.mock('@/lib/queries', () => ({
  useFrontmapTokens: () => q({ tokens: h.tokens, count: h.tokens.length, engine: 'frontmap' }),
  useFrontmapPrimitives: () => q({ primitives: h.primitives, count: h.primitives.length, engine: 'frontmap' }),
  useFrontmapRoutes: () => q({ routes: h.routes, count: h.routes.length, engine: 'frontmap' }),
}))

const { FrontmapPanel } = await import('./FrontmapPanel')

describe('FrontmapPanel — les 3 vues du design-system', () => {
  beforeEach(() => {
    h.tokens = [
      { name: '--color-accent-500', value: '#3b82f6', group: 'accent', source_file: 'web/src/index.css', line: 2 },
      { name: '--color-danger-500', value: '#ef4444', group: 'status', source_file: 'web/src/index.css', line: 3 },
    ]
    h.primitives = [{ name: 'Alert', file: 'web/src/components/ui/Alert.tsx', line: 6, lead: 'Encart contextuel' }]
    h.routes = [{ var: 'rootRoute', path: null, full_path: '', component: 'AppShell', parent: null, is_root: true, file: 'web/src/router.tsx', line: 15 }]
  })

  it('vue par défaut = Tokens, groupés par `group` (sous-titres accent/status)', () => {
    render(<FrontmapPanel project="demo" />)
    expect(screen.getByText('--color-accent-500')).toBeInTheDocument()
    expect(screen.getByText('accent')).toBeInTheDocument()
    expect(screen.getByText('status')).toBeInTheDocument()
  })

  it('switch → Routes affiche l\'arbre des routes (full_path → composant)', () => {
    render(<FrontmapPanel project="demo" />)
    fireEvent.click(screen.getByRole('tab', { name: /Routes/ }))
    expect(screen.getByText(/AppShell/)).toBeInTheDocument()
    expect(screen.getByText('/')).toBeInTheDocument()               // full_path vide → '/'
  })

  it('tokens vides → EmptyState honnête (pas un panneau muet)', () => {
    h.tokens = []
    render(<FrontmapPanel project="demo" />)
    expect(screen.getByText('Aucun token')).toBeInTheDocument()
  })
})
