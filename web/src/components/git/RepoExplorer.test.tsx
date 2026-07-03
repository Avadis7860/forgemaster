import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RepoExplorer } from './RepoExplorer'
import type { GitBranch } from '@/lib/schemas'

// Arbre injectable par chemin + blob injectable par fichier (hoistés pour les factories vi.mock). On isole
// des vraies requêtes : seule la NAVIGATION (descente dossier, sélection fichier, états binaire/tronqué) est
// testée ici.
const h = vi.hoisted(() => ({
  trees: {} as Record<string, { name: string; type: string; size: number | null; sha: string }[]>,
  blobs: {} as Record<string, unknown>,
  history: [] as { sha: string; short: string; author: string; date: string; subject: string }[],
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

describe('RepoExplorer', () => {
  it('liste l\'arbre, descend dans un dossier, ouvre un fichier avec n° de ligne', async () => {
    h.trees = {
      '': [
        { name: 'src', type: 'tree', size: null, sha: 't1' },
        { name: 'README.md', type: 'blob', size: 12, sha: 'b1' },
      ],
      src: [{ name: 'app.py', type: 'blob', size: 20, sha: 'b2' }],
    }
    h.blobs = {
      'src/app.py': { project: 'p', path: 'src/app.py', ref: 'dev', size: 20,
        binary: false, truncated: false, too_large: false, content: "print('hi')\nx = 1\n" },
    }
    render(<RepoExplorer project="p" branches={BRANCHES} />)

    // arbre racine : dossier + fichier présents
    expect(screen.getByText('src')).toBeInTheDocument()
    expect(screen.getByText('README.md')).toBeInTheDocument()
    // aucun fichier sélectionné au départ
    expect(screen.getByText('Aucun fichier')).toBeInTheDocument()

    // descente dans src → app.py visible
    fireEvent.click(screen.getByText('src'))
    expect(await screen.findByText('app.py')).toBeInTheDocument()

    // ouverture du fichier → contenu + n° de ligne (gutter 1 et 2)
    fireEvent.click(screen.getByText('app.py'))
    expect(await screen.findByText("print('hi')")).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('signale un fichier binaire sans émettre d\'octets', async () => {
    h.trees = { '': [{ name: 'data.bin', type: 'blob', size: 999, sha: 'b3' }] }
    h.blobs = {
      'data.bin': { project: 'p', path: 'data.bin', ref: 'dev', size: 999,
        binary: true, truncated: false, too_large: false, content: '' },
    }
    render(<RepoExplorer project="p" branches={BRANCHES} />)
    fireEvent.click(screen.getByText('data.bin'))
    expect(await screen.findByText('Fichier binaire')).toBeInTheDocument()
  })

  it('signale un fichier trop volumineux', async () => {
    h.trees = { '': [{ name: 'big.log', type: 'blob', size: 20_000_000, sha: 'b4' }] }
    h.blobs = {
      'big.log': { project: 'p', path: 'big.log', ref: 'dev', size: 20_000_000,
        binary: false, truncated: true, too_large: true, content: '' },
    }
    render(<RepoExplorer project="p" branches={BRANCHES} />)
    fireEvent.click(screen.getByText('big.log'))
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
    render(<RepoExplorer project="p" branches={BRANCHES} />)
    fireEvent.click(screen.getByText('README.md'))
    expect(await screen.findByText('# projet')).toBeInTheDocument()      // corps par défaut
    // basculer vers l'historique : les 2 commits récents d'abord apparaissent
    fireEvent.click(screen.getByText('Historique'))
    expect(await screen.findByText('doc: maj')).toBeInTheDocument()
    expect(screen.getByText('init')).toBeInTheDocument()
    expect(screen.getByText('Alice')).toBeInTheDocument()
  })
})
