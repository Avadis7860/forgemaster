import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RepoExplorer } from './RepoExplorer'
import type { GitBranch } from '@/lib/schemas'

// Arbre injectable par chemin + blob injectable par fichier (hoistés pour les factories vi.mock). On isole
// des vraies requêtes : seule la NAVIGATION (descente dossier, sélection fichier, états binaire/tronqué) est
// testée ici. `search` = l'état d'URL simulé (le router est mocké, cf. convention GitExplorer.test) : les
// setters de RepoExplorer passent par useNavigate → on mute `search` puis on `rerender` pour refléter l'URL.
const h = vi.hoisted(() => ({
  search: {} as { ref?: string; path?: string; file?: string },
  trees: {} as Record<string, { name: string; type: string; size: number | null; sha: string }[]>,
  blobs: {} as Record<string, unknown>,
  history: [] as { sha: string; short: string; author: string; date: string; subject: string }[],
}))

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => h.search,
  // Applique l'updater à l'état d'URL simulé (le vrai router re-rendrait tout seul ; en test on rerender()).
  useNavigate: () => (args: { to: string; search?: (p: object) => object }) => {
    h.search = (typeof args.search === 'function' ? args.search(h.search) : args.search ?? {}) as typeof h.search
  },
}))

vi.mock('@/lib/queries', () => ({
  useGitTree: (_p: string, _r: string, path: string) => ({
    data: { project: 'p', ref: 'dev', path, entries: h.trees[path] ?? [] },
    isLoading: false, isError: false, error: null,
  }),
  useGitBlob: (_p: string, _r: string, file: string) => ({
    data: file ? h.blobs[file] : undefined,
    isLoading: false, isError: false, error: null,
  }),
  useGitHistory: (_p: string, _r: string, file: string) => ({
    data: file ? { project: 'p', ref: 'dev', path: file, commits: h.history } : undefined,
    isLoading: false, isError: false, error: null,
  }),
}))

const BRANCHES: GitBranch[] = [
  { name: 'dev', sha: 'aaa', subject: 's' },
  { name: 'main', sha: 'bbb', subject: 's' },
]

const TAGS: GitBranch[] = [{ name: 'v1.0', sha: 'ttt', subject: 'release 1.0' }]

beforeEach(() => {
  h.search = {}
  h.history = []
})

describe('RepoExplorer', () => {
  it('liste l\'arbre, descend dans un dossier, ouvre un fichier avec n° de ligne', async () => {
    // Racine SANS README → l'invite « Aucun fichier » reste l'état par défaut (le README-auto a son test dédié).
    h.trees = {
      '': [
        { name: 'src', type: 'tree', size: null, sha: 't1' },
        { name: 'notes.txt', type: 'blob', size: 12, sha: 'b1' },
      ],
      src: [{ name: 'app.txt', type: 'blob', size: 20, sha: 'b2' }],
    }
    h.blobs = {
      'src/app.txt': { project: 'p', path: 'src/app.txt', ref: 'dev', size: 20,
        binary: false, truncated: false, too_large: false, content: "print('hi')\nx = 1\n" },
    }
    const { rerender } = render(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)

    // arbre racine : dossier + fichier présents
    expect(screen.getByText('src')).toBeInTheDocument()
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
    // aucun fichier sélectionné au départ
    expect(screen.getByText('Aucun fichier')).toBeInTheDocument()

    // descente dans src (navigation URL) → rerender pour refléter ?path=src → app.txt visible
    fireEvent.click(screen.getByText('src'))
    rerender(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    expect(await screen.findByText('app.txt')).toBeInTheDocument()

    // ouverture du fichier (navigation URL) → rerender → contenu + n° de ligne (gutter 1 et 2)
    fireEvent.click(screen.getByText('app.txt'))
    rerender(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    expect(await screen.findByText("print('hi')")).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('auto-rend le README.md du dossier en Markdown (aucune sélection requise, façon GitHub)', async () => {
    h.trees = {
      '': [
        { name: 'src', type: 'tree', size: null, sha: 't1' },
        { name: 'README.md', type: 'blob', size: 22, sha: 'b1' },
      ],
    }
    h.blobs = {
      'README.md': { project: 'p', path: 'README.md', ref: 'dev', size: 22,
        binary: false, truncated: false, too_large: false, content: '# Titre\n\nCorps du readme.' },
    }
    render(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    // Le README mène : plus d'invite « Aucun fichier », et le `#` est rendu en <h1> (DocView), pas laissé brut.
    expect(screen.queryByText('Aucun fichier')).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Titre' })).toBeInTheDocument()
  })

  it('colore un fichier de code reconnu (lowlight) en préservant la grille n° de ligne', async () => {
    h.trees = { '': [{ name: 'main.py', type: 'blob', size: 30, sha: 'bp' }] }
    h.blobs = {
      'main.py': { project: 'p', path: 'main.py', ref: 'dev', size: 30,
        binary: false, truncated: false, too_large: false, content: 'import os\nx = 1\n' },
    }
    const { rerender } = render(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /main\.py/ }))
    rerender(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    // `.py` reconnu → lowlight : `import` devient un token coloré (span .hljs-keyword), dans un conteneur `hljs`
    // qui garde la grille n°-de-ligne (chunk lazy résolu par le Suspense).
    const kw = await screen.findByText('import')
    expect(kw).toHaveClass('hljs-keyword')
    expect(kw.closest('code')).toHaveClass('hljs')
  })

  it('signale un fichier binaire sans émettre d\'octets', async () => {
    h.trees = { '': [{ name: 'data.bin', type: 'blob', size: 999, sha: 'b3' }] }
    h.blobs = {
      'data.bin': { project: 'p', path: 'data.bin', ref: 'dev', size: 999,
        binary: true, truncated: false, too_large: false, content: '' },
    }
    const { rerender } = render(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    fireEvent.click(screen.getByText('data.bin'))
    rerender(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    expect(await screen.findByText('Fichier binaire')).toBeInTheDocument()
  })

  it('signale un fichier trop volumineux', async () => {
    h.trees = { '': [{ name: 'big.log', type: 'blob', size: 20_000_000, sha: 'b4' }] }
    h.blobs = {
      'big.log': { project: 'p', path: 'big.log', ref: 'dev', size: 20_000_000,
        binary: false, truncated: true, too_large: true, content: '' },
    }
    const { rerender } = render(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    fireEvent.click(screen.getByText('big.log'))
    rerender(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    expect(await screen.findByText('Fichier trop volumineux')).toBeInTheDocument()
  })

  it('bascule vers l\'historique du fichier ouvert (P3)', async () => {
    h.trees = { '': [{ name: 'README.md', type: 'blob', size: 12, sha: 'b1' }] }
    h.blobs = {
      'README.md': { project: 'p', path: 'README.md', ref: 'dev', size: 12,
        binary: false, truncated: false, too_large: false, content: '# projet\n' },
    }
    h.history = [
      { sha: 'c2full', short: 'c2', author: 'Alice', date: '2026-07-03T10:00:00Z', subject: 'doc: maj' },
      { sha: 'c1full', short: 'c1', author: 'Bob', date: '2026-07-01T10:00:00Z', subject: 'init' },
    ]
    const { rerender } = render(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    // Racine = README seul → il s'auto-rend (ReadmePane) : pas de bascule Historique tant qu'aucun fichier
    // n'est sélectionné. On clique le bouton d'ARBRE (pas l'en-tête du README auto) pour ouvrir la visionneuse.
    expect(screen.queryByText('Historique')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /README\.md/ }))
    rerender(<RepoExplorer project="p" branches={BRANCHES} tags={[]} />)
    // fichier sélectionné → visionneuse avec bascule Historique ; le `.md` est rendu en Markdown (<h1>projet</h1>).
    expect(await screen.findByText('Historique')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'projet' })).toBeInTheDocument()
    // basculer vers l'historique (état LOCAL showHistory → re-render auto) : les 2 commits récents d'abord.
    fireEvent.click(screen.getByText('Historique'))
    expect(await screen.findByText('doc: maj')).toBeInTheDocument()
    expect(screen.getByText('init')).toBeInTheDocument()
    expect(screen.getByText('Alice')).toBeInTheDocument()
  })

  it('sépare branches et tags dans le sélecteur de réf (optgroups) et checkout d\'un tag', () => {
    h.trees = { '': [{ name: 'notes.txt', type: 'blob', size: 3, sha: 'b1' }] }
    render(<RepoExplorer project="p" branches={BRANCHES} tags={TAGS} />)
    // deux optgroups distincts : Branches (dev/main) + Tags (v1.0)
    const branchesGroup = screen.getByRole('group', { name: 'Branches' })
    const tagsGroup = screen.getByRole('group', { name: 'Tags' })
    expect(branchesGroup).toContainElement(screen.getByRole('option', { name: 'main' }))
    expect(tagsGroup).toContainElement(screen.getByRole('option', { name: 'v1.0' }))
    // sélectionner le tag écrit ?ref=v1.0 (et remet path/file à zéro), via l'updater d'URL simulé
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'v1.0' } })
    expect(h.search).toEqual({ ref: 'v1.0', path: undefined, file: undefined })
  })
})
