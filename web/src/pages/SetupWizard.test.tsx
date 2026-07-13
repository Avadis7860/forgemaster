import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SetupWizard } from './SetupWizard'

// État d'onboarding injectable (hoisté pour la factory vi.mock). On isole des vraies requêtes : seul le
// SÉQUENÇAGE des étapes selon l'état (first_run vs tout-réglé) est testé ici.
const h = vi.hoisted(() => ({
  data: undefined as unknown,
  boot: undefined as unknown,   // aperçu bootstrap injectable ; undefined → défaut « aucun manifeste »
  runMutate: vi.fn(),
}))

vi.mock('@/lib/queries', () => ({
  useOnboarding: () => ({ data: h.data, isLoading: false, isError: false, error: null, refetch: vi.fn(), isFetching: false }),
  useTypes: () => ({ data: [{ type: 'generic', version: '1', facets: ['doc'], default_facet: 'doc' }], isPending: false }),
  useCreateProject: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useSetMirror: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useLinkCredential: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useUnlinkCredential: () => ({ mutate: vi.fn(), isPending: false }),
  useBootstrap: () => ({
    data: h.boot ?? { available: false, tools: [], adopted: 0, total: 0 },
    isLoading: false,
  }),
  useRunBootstrap: () => ({ mutate: h.runMutate, isPending: false, isError: false, isSuccess: false, error: null, data: undefined }),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useNavigate: () => vi.fn(),
}))

const STORE = { backend: 'file', ready: true, detail: 'coffre fichier chiffré' }
const AUTHED = { authenticated: true, source: 'credentials-file' }
const UNAUTHED = { authenticated: false, source: null }

describe('SetupWizard', () => {
  it('instance neuve (first_run) : guide bienvenue → coffre → 1er projet, avec le formulaire de création', () => {
    h.data = { secret_store: STORE, requirements: [], complete: true, project_count: 0, first_run: true, claude_auth: AUTHED }
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
      claude_auth: AUTHED,
    }
    render(<SetupWizard />)
    expect(screen.getByText('Ton cockpit est prêt')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Créer ce projet' })).toBeNull() // projet déjà là
  })

  it('outils du framework : un manifeste disponible propose de ranger la boîte à outils d’un clic', () => {
    h.data = { secret_store: STORE, requirements: [], complete: true, project_count: 0, first_run: true, claude_auth: AUTHED }
    h.boot = {
      available: true,
      total: 2,
      adopted: 0,
      tools: [
        { slug: 'code-map', source_url: 'https://github.com/x/code-map.git', kind: 'tool', adopted: false },
        { slug: 'docs-map', source_url: 'https://github.com/x/docs-map.git', kind: 'tool', adopted: false },
      ],
    }
    render(<SetupWizard />)
    expect(screen.getByText('Outils du framework')).toBeInTheDocument()
    expect(screen.getByText('code-map')).toBeInTheDocument()
    // le bouton d'amorçage adopte les outils du manifeste (2 restants)
    const install = screen.getByRole('button', { name: /Installer la boîte à outils/ })
    install.click()
    expect(h.runMutate).toHaveBeenCalled()
    h.boot = undefined   // restaure le défaut pour les tests suivants
  })

  it('token de push en attente : le wizard renvoie vers Réglages, ne propose PAS de lier le token lui-même', () => {
    h.data = {
      secret_store: STORE,
      requirements: [{ project: 'p', mirror_remote: 'https://github.com/a/p.git', needs_credential: true, linked: false, satisfied: false }],
      complete: false,
      project_count: 1,
      first_run: false,
      claude_auth: AUTHED,
    }
    render(<SetupWizard />)
    // le wizard ne duplique plus l'affordance de liaison (retirée — elle vit dans Réglages)
    expect(screen.queryByRole('button', { name: 'Lier un token' })).toBeNull()
    expect(screen.queryByText('Ton cockpit est prêt')).toBeNull()      // pas « prêt » tant qu'un token manque
    expect(screen.getByText(/token de push/i)).toBeInTheDocument()      // renvoi explicite
    expect(screen.getByRole('button', { name: 'Réglages (miroirs & tokens)' })).toBeInTheDocument()
  })

  it('compte Claude non connecté : l’étape affiche l’instruction `claude login` (jamais silencieux)', () => {
    h.data = { secret_store: STORE, requirements: [], complete: true, project_count: 0, first_run: true, claude_auth: UNAUTHED }
    render(<SetupWizard />)
    expect(screen.getByText('Compte Claude')).toBeInTheDocument()
    expect(screen.getByText('Aucun compte Claude sur cette machine')).toBeInTheDocument()
    expect(screen.getByText(/claude login/)).toBeInTheDocument()
  })

  it('compte Claude connecté : l’étape le confirme, sans instruction de login', () => {
    h.data = { secret_store: STORE, requirements: [], complete: true, project_count: 0, first_run: true, claude_auth: AUTHED }
    render(<SetupWizard />)
    expect(screen.getByText('Compte Claude · connecté')).toBeInTheDocument()
    expect(screen.queryByText('Aucun compte Claude sur cette machine')).toBeNull()
  })
})
