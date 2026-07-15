import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { deploymentTone } from '@/lib/statusTone'

const h = vi.hoisted(() => ({ deployments: [] as unknown[] }))

const noopMut = () => ({ mutate: vi.fn(), isPending: false, error: null })

vi.mock('@/lib/queries', () => ({
  useDeployments: () => ({
    data: { project: 'svc', deployments: h.deployments },
    isLoading: false, isError: false, error: null, refetch: vi.fn(), isFetching: false,
  }),
  useReconcileDeployments: () => ({ mutate: vi.fn(), isPending: false }),
  useDeploy: noopMut,
  useStopDeployment: noopMut,
  useRestartDeployment: noopMut,
  useDeploymentLogs: () => ({ data: { lines: [] }, isError: false, error: null, isFetching: false, refetch: vi.fn() }),
}))

const { RuntimeStrip } = await import('./RuntimeStrip')

const dep = (over: Record<string, unknown>) =>
  ({ branch: 'dev', status: 'no_deploy', port: null, url: null, last_deploy_sha: null, ...over })

describe('RuntimeStrip', () => {
  beforeEach(() => { h.deployments = [] })

  it('déploiement running → lien health-gated ACTIF (href présent) + statut « en marche »', () => {
    h.deployments = [dep({ status: 'running', port: 5250, url: 'http://127.0.0.1:5250' })]
    render(<RuntimeStrip project="svc" />)
    expect(screen.getByText('en marche')).toBeInTheDocument()
    // Le nom de branche n'est plus répété dans le lien (axe 5 : porté par le badge) — libellé « ouvrir ↗ ».
    const link = screen.getByRole('link', { name: /ouvrir/ })
    expect(link).toHaveAttribute('href', 'http://127.0.0.1:5250')
  })

  it('déploiement stopped → lien INERTE (aucun href) + jamais un faux-vert', () => {
    h.deployments = [dep({ status: 'stopped', port: 5250, url: 'http://127.0.0.1:5250' })]
    render(<RuntimeStrip project="svc" />)
    expect(screen.getByText('arrêté')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /ouvrir/ })).not.toBeInTheDocument()   // pas de lien mort
  })

  it('jamais déployé → « jamais déployé » (vide honnête, pas une erreur)', () => {
    h.deployments = [dep({ status: 'no_deploy' })]
    render(<RuntimeStrip project="svc" />)
    expect(screen.getByText('jamais déployé')).toBeInTheDocument()
  })
})

describe('deploymentTone (aucun faux-vert)', () => {
  it('seul running est vert ; unhealthy=danger, stopped/no_deploy=neutral', () => {
    expect(deploymentTone('running')).toBe('ok')
    expect(deploymentTone('unhealthy')).toBe('danger')
    expect(deploymentTone('stopped')).toBe('neutral')
    expect(deploymentTone('no_deploy')).toBe('neutral')
    expect(deploymentTone('building')).toBe('info')
    expect(deploymentTone('???')).toBe('neutral')   // état inconnu → repli neutre, jamais vert
  })
})
