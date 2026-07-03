import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CommitDetailCard, DiffCard } from './GitIntelligence'
import type { GitBranch } from '@/lib/schemas'

// Détail de commit + diff injectables (hoistés pour les factories vi.mock). On isole des vraies requêtes :
// seul le RENDU (métadonnées + fichiers touchés, coloration du diff, état « aucune différence ») est testé.
const h = vi.hoisted(() => ({
  commit: null as unknown,
  diff: null as unknown,
}))

vi.mock('@/lib/queries', () => ({
  useGitCommit: () => ({ data: h.commit, isLoading: false, isError: false, error: null }),
  useGitDiff: () => ({ data: h.diff, isLoading: false, isError: false, error: null }),
}))

const BRANCHES: GitBranch[] = [
  { name: 'dev', sha: 'aaa', subject: 's' },
  { name: 'main', sha: 'bbb', subject: 's' },
]

describe('CommitDetailCard', () => {
  it('rend les métadonnées + les fichiers touchés avec +/- et le drapeau binaire', () => {
    h.commit = {
      project: 'p', sha: 'deadbeefcafe', short: 'deadbee', author: 'Alice',
      email: 'a@x.invalid', date: '2026-07-03T10:00:00Z', subject: 'feat: truc', body: 'corps du message',
      files: [
        { path: 'src/app.py', binary: false, additions: 3, deletions: 1 },
        { path: 'logo.png', binary: true, additions: null, deletions: null },
      ],
    }
    const onClose = vi.fn()
    render(<CommitDetailCard project="p" sha="deadbeefcafe" onClose={onClose} />)
    expect(screen.getByText('feat: truc')).toBeInTheDocument()
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('corps du message')).toBeInTheDocument()
    expect(screen.getByText('src/app.py')).toBeInTheDocument()
    expect(screen.getByText('+3')).toBeInTheDocument()
    expect(screen.getByText('−1')).toBeInTheDocument()
    expect(screen.getByText('binaire')).toBeInTheDocument()   // fichier binaire → pas de compte de lignes
    fireEvent.click(screen.getByText('Fermer'))
    expect(onClose).toHaveBeenCalledOnce()
  })
})

describe('DiffCard', () => {
  it('rend un diff unifié (lignes ajoutées/retirées)', () => {
    h.diff = {
      project: 'p', base: 'main', head: 'dev', files: ['a.py'],
      diff: 'diff --git a/a.py b/a.py\n@@ -1 +1,2 @@\n-old line\n+new line\n+extra\n',
    }
    render(<DiffCard project="p" branches={BRANCHES} />)
    expect(screen.getByText('1 fichier(s) changé(s)')).toBeInTheDocument()
    expect(screen.getByText('-old line')).toBeInTheDocument()
    expect(screen.getByText('+new line')).toBeInTheDocument()
    expect(screen.getByText('+extra')).toBeInTheDocument()
  })

  it('affiche « aucune différence » quand le diff est vide', () => {
    h.diff = { project: 'p', base: 'main', head: 'dev', files: [], diff: '' }
    render(<DiffCard project="p" branches={BRANCHES} />)
    expect(screen.getByText('Aucune différence')).toBeInTheDocument()
  })
})
