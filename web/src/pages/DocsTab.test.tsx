import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ docs: undefined as unknown }))

vi.mock('@/lib/queries', () => ({
  useDocs: () => ({ data: h.docs, isLoading: false, isError: false, error: null }),
}))
vi.mock('@tanstack/react-router', () => ({ useParams: () => ({ project: 'code-map' }) }))
// react-markdown est lourd + lazy → on stube le renderer (on teste le câblage de l'onglet, pas le Markdown).
vi.mock('@/components/docs/DocView', () => ({
  DocView: ({ content }: { content: string }) => <div data-testid="docview">{content}</div>,
}))

const { DocsTab } = await import('./DocsTab')

describe('DocsTab', () => {
  beforeEach(() => { h.docs = undefined })

  it('carte présente → rend le contenu Markdown (via DocView lazy)', async () => {
    h.docs = { found: true, path: 'docs/tool-card.md', content: '# code-map', truncated: false }
    render(<DocsTab />)
    expect(await screen.findByTestId('docview')).toHaveTextContent('# code-map')
  })

  it('pas de carte → EmptyState actionnable (pas une erreur)', () => {
    h.docs = { found: false, path: null, content: '', truncated: false }
    render(<DocsTab />)
    expect(screen.getByText('Pas encore de page docs')).toBeInTheDocument()
  })
})
