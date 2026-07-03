import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SetupWizard } from './SetupWizard'

// État d'onboarding injectable (hoisté pour la factory vi.mock). On isole des vraies requêtes : seul le
// SÉQUENÇAGE des étapes selon l'état (first_run vs tout-réglé) est testé ici.
const h = vi.hoisted(() => ({ data: undefined as unknown }))

vi.mock('@/lib/queries', () => ({
  useOnboarding: () => ({ data: h.data, isLoading: false, isError: false, error: null, refetch: vi.fn(), isFetching: false }),
  useCreateProject: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useSetMirror: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useLinkCredential: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useUnlinkCredential: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useNavigate: () => vi.fn(),
}))

const STORE = { backend: 'file', ready: true, detail: 'coffre fichier chiffré' }

describe('SetupWizard', () => {
  it('instance neuve (first_run) : guide bienvenue → coffre → 1er projet, avec le formulaire de création', () => {
    h.data = { secret_store: STORE, requirements: [], complete: true, project_count: 0, first_run: true }
    render(<SetupWizard />)
    expect(screen.getByText('Bienvenue')).toBeInTheDocument()
    expect(screen.getByText('Coffre de secrets')).toBeInTheDocument()
    expect(screen.getByText('Ton premier projet')).toBeInTheDocument()
    // le formulaire de création est présent (source unique NewProjectForm)
    expect(screen.getByRole('button', { name: 'Créer ce projet' })).toBeInTheDocument()
    // pas de faux « prêt » sur une instance neuve
    expect(screen.queryByText('Ton cockpit est prêt')).toBeNull()
  })

  it('tout réglé : annonce que le cockpit est prêt', () => {
    h.data = {
      secret_store: STORE,
      requirements: [{ project: 'p', mirror_remote: null, needs_credential: false, linked: false, satisfied: true }],
      complete: true,
      project_count: 1,
      first_run: false,
    }
    render(<SetupWizard />)
    expect(screen.getByText('Ton cockpit est prêt')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Créer ce projet' })).toBeNull() // projet déjà là
  })

  it('token de push en attente : le wizard renvoie vers Réglages, ne propose PAS de lier le token lui-même', () => {
    h.data = {
      secret_store: STORE,
      requirements: [{ project: 'p', mirror_remote: 'https://github.com/a/p.git', needs_credential: true, linked: false, satisfied: false }],
      complete: false,
      project_count: 1,
      first_run: false,
    }
    render(<SetupWizard />)
    // le wizard ne duplique plus l'affordance de liaison (retirée — elle vit dans Réglages)
    expect(screen.queryByRole('button', { name: 'Lier un token' })).toBeNull()
    expect(screen.queryByText('Ton cockpit est prêt')).toBeNull()      // pas « prêt » tant qu'un token manque
    expect(screen.getByText(/token de push/i)).toBeInTheDocument()      // renvoi explicite
    expect(screen.getByRole('button', { name: 'Réglages (miroirs & tokens)' })).toBeInTheDocument()
  })
})
