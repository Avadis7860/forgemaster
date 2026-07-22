import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// État injectable (hoisté pour les factories vi.mock). On teste la règle du badge de type de bundle sur
// l'entête : rendu SEULEMENT si le type est distinctif (≠ generic) — sinon absence honnête, jamais un faux type.
const h = vi.hoisted(() => ({ project: undefined as Record<string, unknown> | undefined }))

vi.mock('@/lib/queries', () => ({
  useProject: () => ({ data: h.project, isError: false, error: null }),
}))
vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ project: 'demo' }),
  Outlet: () => null,
}))
// WorkspaceTabs tire des Link routeur → stub (on teste l'entête, pas les onglets).
vi.mock('@/components/WorkspaceTabs', () => ({ WorkspaceTabs: () => null }))

const { ProjectWorkspace } = await import('./ProjectWorkspace')

const BASE = {
  id: '1', slug: 'demo', name: 'Demo', sot_path: '/x/sot.git', mirror_remote: null,
  backend: 'internal', kind: 'project', owner: null, credential_ref: null, created_at: 't',
}

describe('ProjectWorkspace — badge de type de bundle', () => {
  beforeEach(() => { h.project = undefined })

  it('rend le type de bundle quand il est distinctif (≠ generic)', () => {
    h.project = { ...BASE, project_type: 'service-api' }
    render(<ProjectWorkspace />)
    expect(screen.getByText('service-api')).toBeInTheDocument()
  })

  it("n'affiche PAS de badge pour un type generic (état vide honnête, pas un faux type)", () => {
    h.project = { ...BASE, project_type: 'generic' }
    render(<ProjectWorkspace />)
    expect(screen.queryByText('generic')).not.toBeInTheDocument()
    // le badge backend, lui, reste rendu (on n'a rien cassé de l'entête existante)
    expect(screen.getByText('internal')).toBeInTheDocument()
  })
})
