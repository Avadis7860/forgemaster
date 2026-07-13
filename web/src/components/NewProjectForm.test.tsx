import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NewProjectForm } from './NewProjectForm'

// Spy de mutation hoisté (P3 : on vérifie que le type sélectionné remonte bien dans le payload de création).
const h = vi.hoisted(() => ({ mutate: vi.fn() }))

vi.mock('@/lib/queries', () => ({
  useCreateProject: () => ({ mutate: h.mutate, isPending: false, isError: false, error: null }),
  useTypes: () => ({
    data: [
      { type: 'generic', version: '1', facets: ['doc'], default_facet: 'doc' },
      { type: 'browser-game', version: '1', facets: ['frontend', 'backend', 'game-design', 'doc'], default_facet: 'backend' },
    ],
    isPending: false,
  }),
}))

describe('NewProjectForm', () => {
  it('le dropdown liste les types du registre (registre-driven, pas en dur)', () => {
    render(<NewProjectForm />)
    const select = screen.getByLabelText('type de projet')
    const values = Array.from(select.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(values).toEqual(['generic', 'browser-game'])
  })

  it('le type sélectionné remonte dans le payload de création (défaut generic)', () => {
    render(<NewProjectForm />)
    fireEvent.change(screen.getByLabelText('slug du projet'), { target: { value: 'void-runner' } })
    fireEvent.change(screen.getByLabelText('type de projet'), { target: { value: 'browser-game' } })
    fireEvent.click(screen.getByRole('button', { name: 'Créer le projet' }))
    expect(h.mutate).toHaveBeenCalledTimes(1)
    expect(h.mutate.mock.calls[0][0]).toEqual({
      slug: 'void-runner',
      name: null,
      mirror_remote: null,
      project_type: 'browser-game',
    })
  })
})
