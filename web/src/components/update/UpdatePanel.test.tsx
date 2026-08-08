import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import type { UpdateRun, Version } from '@/lib/schemas'

const MORT = new ApiError(0, 'daemon injoignable (TypeError: Failed to fetch)')
const MAPS = ['code-map', 'docs-map', 'front-map']
const SHA_A = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2'
const SHA_B = '9f8e7d6c5b4a9f8e7d6c5b4a9f8e7d6c5b4a9f8e'

const h = vi.hoisted(() => ({
  runs: null as unknown,
  runsError: null as unknown,
  run: null as unknown,
  runError: null as unknown,
  contact: 0,
  aptitude: null as unknown,
  version: null as unknown,
}))

vi.mock('@/lib/queries', () => ({
  useUpdateRuns: () => ({
    data: h.runs, error: h.runsError, isFetching: false, dataUpdatedAt: h.contact, refetch: vi.fn(),
  }),
  useUpdateRun: () => ({
    data: h.run, error: h.runError, isFetching: false, dataUpdatedAt: h.contact, refetch: vi.fn(),
  }),
  useVersion: () => ({
    data: h.version, error: null, isFetching: false, dataUpdatedAt: h.contact, refetch: vi.fn(),
  }),
  useUpdateAptitude: () => ({ data: h.aptitude, error: null, isPending: false, refetch: vi.fn() }),
  useApplyUpdate: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, error: null }),
  useRollbackUpdate: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, error: null }),
  // Consommés par les enfants — neutralisés ici : ce fichier teste l'orchestration, pas l'aire de dépôt.
  useWheels: () => ({ data: { wheels: [], total: 0, keep: 3, max_bytes: 67108864 }, isPending: false,
                      isError: false, error: null }),
  useStageWheel: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false,
                          data: undefined, error: null }),
  useUpdatePlan: () => ({ data: null, error: null, isError: false, isPending: true }),
}))

const { UpdatePanel } = await import('./UpdatePanel')

function run(over: Partial<UpdateRun> = {}): UpdateRun {
  return {
    run: '2026-08-07T10-00-00Z', mode: 'apply', scope: 'user', unit: 'u', started_at: null,
    target: null, state: 'running', rc: null, verdict: '', impact: null, journal: '', ...over,
  }
}

function apte(over: Record<string, unknown> = {}) {
  return {
    deployable: { ok: true, reason: 'l\'unité … lance le lien stable …' },
    reversible: {
      ok: true, reason: '',
      target: { snapshot: '2026-08-01T00-00-00Z', path: '/h/snapshots/2026-08-01T00-00-00Z',
                venv: '/h/venvs/2026-08-01T00-00-00Z' },
    },
    ...over,
  }
}

function version(over: Partial<Version> = {}): Version {
  return {
    version: '0.1.0', sha: 'abcdef1234', committed_at: '2026-08-07T00:00:00Z',
    comparable: false, stale: null, behind_by: null, missing_types: [],
    reference: null, head: null,
    // Défaut : une instance d'ÉDITION conforme. Chaque bloc qui parle d'édition la repose explicitement.
    install: { mode: 'edition', reason: null },
    maps: MAPS.map((name) => ({ name, sha: SHA_A, requested_ref: null, source: 'edition', reason: null })),
    edition: {
      edition_dir: '/opt/fm/site-packages/forgemaster/_maps', reason: null, state: 'up-to-date',
      maps: MAPS.map((name) => ({ name, served: SHA_A, edition: SHA_A, state: 'up-to-date',
                                  reason: null })),
    },
    mcp: { topology: 'none', sha: null, endpoint: null, reason: 'aucun endpoint MCP configuré' },
    ...over,
  }
}

const CALME = { runs: [], total: 0, truncated: false, follow_timeout: 900 }

// Défaut de TOUS les blocs : une provenance sans référence locale (le cas d'une install publique). Chaque
// bloc qui parle de fraîcheur la repose explicitement.
beforeEach(() => { h.version = version() })

describe('UpdatePanel — l\'aptitude, DITE AU REPOS', () => {
  beforeEach(() => {
    h.runs = CALME; h.runsError = null; h.run = null; h.runError = null; h.contact = Date.now()
  })

  it('dit VERS QUOI on reviendrait, sans qu\'on ait cliqué', () => {
    // « Dire tôt » vaut dans les deux sens. Une surface qui ne parle que pour refuser laisse l'utilisateur
    // sans réponse le jour où tout va bien — c'est-à-dire tous les jours sauf un.
    h.aptitude = apte()

    render(<UpdatePanel />)

    expect(screen.getByText(/Revenir en arrière/)).toBeInTheDocument()
    // Les DEUX moitiés de « vers quoi » : l'instantané (correspondance exacte, donc son propre span) et
    // le binaire. Dire l'un sans l'autre laisserait la question à moitié répondue.
    expect(screen.getByText('2026-08-01T00-00-00Z')).toBeInTheDocument()
    expect(screen.getByText('/h/venvs/2026-08-01T00-00-00Z')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Voir le retour arrière/ })).toBeInTheDocument()
  })

  it('désarme l\'affordance et NE DIT QU\'UN REFUS quand le socle refuse', () => {
    // LE cas de la fiche : une instance jamais migrée vers le lien stable. `reversible.ok` vaut `null` —
    // rien n'a été mesuré — donc on pointe la cause au lieu d'écrire un second motif. Deux refus pour une
    // seule cause feraient chercher deux réparations.
    h.aptitude = apte({
      deployable: { ok: false, reason: 'l\'unité … lance un venv EN DUR … `forgemaster install-service`' },
      reversible: { ok: null, reason: 'indéterminé tant que le socle …', target: null },
    })

    render(<UpdatePanel />)

    expect(screen.getByText(/venv EN DUR/)).toBeInTheDocument()
    expect(screen.getByText(/install-service/)).toBeInTheDocument()
    expect(screen.queryByText(/indéterminé tant que/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Voir le retour arrière/ })).not.toBeInTheDocument()
  })

  it('remplace la promesse générique par le motif RÉEL quand il n\'y a pas de cible', () => {
    // Dire « binaire et données, ou rien » au-dessus d'un bouton mort promettrait une capacité que
    // l'instance n'a pas. Le motif du daemon prend la place de la phrase.
    h.aptitude = apte({
      reversible: { ok: false, target: null,
                    reason: 'aucun instantané sous /h/snapshots — il n\'y a rien vers quoi revenir.' },
    })

    render(<UpdatePanel />)

    expect(screen.getByText(/rien vers quoi revenir/)).toBeInTheDocument()
    expect(screen.queryByText(/binaire.*ou rien/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Voir le retour arrière/ })).not.toBeInTheDocument()
  })

  it('ne montre RIEN de l\'aptitude tant que la réponse n\'est pas là', () => {
    // Le contre-témoin de l'affichage au repos : sans donnée, le panneau ne doit ni désarmer (il ferait
    // croire à un refus) ni promettre. Il se tait, et l'affordance reste celle d'avant cette phase.
    h.aptitude = null

    render(<UpdatePanel />)

    expect(screen.getByRole('button', { name: /Voir le retour arrière/ })).toBeInTheDocument()
    expect(screen.getByText(/Revenir en arrière/)).toBeInTheDocument()
  })
})

describe('UpdatePanel — un geste EN VOL désarme les deux affordances', () => {
  // D'OÙ VIENT CE BLOC : la capture `H-running` de la phase 3a·6 — un retour arrière en vol sur VM 9311, et
  // le bouton « Voir le retour arrière » TOUJOURS ARMÉ. La mesure a suivi : deux gestes acceptés à 2 s
  // d'écart, deux applicateurs simultanés, deux verdicts de succès contradictoires.
  beforeEach(() => {
    h.runsError = null; h.run = null; h.runError = null; h.contact = Date.now(); h.aptitude = apte()
  })

  it('retire l\'affordance de retour arrière et DIT pourquoi', () => {
    h.runs = { runs: [run({ state: 'running', mode: 'apply' })], total: 1, truncated: false,
               follow_timeout: 900 }

    render(<UpdatePanel />)

    expect(screen.queryByRole('button', { name: /Voir le retour arrière/ })).not.toBeInTheDocument()
    expect(screen.getByText(/geste de mise à jour est en vol/)).toBeInTheDocument()
    expect(screen.getByText(/2026-08-07T10-00-00Z/)).toBeInTheDocument()
    // La cible d'aptitude a été mesurée AVANT le geste : la promettre pendant qu'il bascule serait promettre
    // un état d'hier. Elle se tait le temps du geste, elle ne ment pas.
    expect(screen.queryByText('/h/venvs/2026-08-01T00-00-00Z')).not.toBeInTheDocument()
  })

  it('ne désarme PAS sur un run mort sans verdict', () => {
    // L'arbitrage, côté surface : `unknown` est un AVEU, pas un geste en vol. Désarmer dessus figerait le
    // panneau POUR TOUJOURS après un run mort — symétrie exacte du refus serveur, qui ne bloque que sur
    // `running`. C'est le contre-témoin qui empêche le désarmement de devenir une prison.
    h.runs = { runs: [run({ state: 'unknown' })], total: 1, truncated: false, follow_timeout: 900 }

    render(<UpdatePanel />)

    expect(screen.getByRole('button', { name: /Voir le retour arrière/ })).toBeInTheDocument()
    expect(screen.queryByText(/geste de mise à jour est en vol/)).not.toBeInTheDocument()
  })

  it('désarme sur le run LE PLUS RÉCENT, même si on en regarde un autre', () => {
    // Le piège que le premier jet aurait posé : le panneau suit le run qu'on OUVRE. Se fier à celui-là
    // laisserait les affordances armées dès qu'on consulte un vieux geste dans l'historique — c'est-à-dire
    // exactement pendant qu'on attend le verdict du geste en cours.
    h.runs = { runs: [run({ run: '2026-08-07T12-04-38Z', state: 'running', mode: 'rollback' }),
                      run({ run: '2026-08-06T09-00-00Z', state: 'done', rc: 0 })],
               total: 2, truncated: false, follow_timeout: 900 }
    h.run = run({ run: '2026-08-06T09-00-00Z', state: 'done', rc: 0, verdict: 'MAJ posée' })

    render(<UpdatePanel />)

    expect(screen.queryByRole('button', { name: /Voir le retour arrière/ })).not.toBeInTheDocument()
    expect(screen.getByText(/retour arrière 2026-08-07T12-04-38Z/)).toBeInTheDocument()
  })
})

describe('UpdatePanel — le verdict de FRAÎCHEUR, sous l\'identité et jamais à côté', () => {
  // D'OÙ VIENT CE BLOC : phase 3b. Le calcul existait depuis longtemps (`build_provenance`) et n'était
  // consommé nulle part ; la 3a·3b a branché le hook en n'en lisant que l'IDENTITÉ, exprès. Ici on lit le
  // reste. Le verdict vit DANS ce panneau parce que l'instance ne s'écrit qu'une fois par page — une
  // seconde carte redirait le sha et la date deux lignes plus bas.
  beforeEach(() => { h.runs = CALME; h.runsError = null; h.run = null; h.runError = null; h.aptitude = apte() })

  it('dit le retard, sa référence et les types apparus depuis', () => {
    h.version = version({ comparable: true, stale: true, behind_by: 12, missing_types: ['site-vitrine'],
                          reference: '/home/u/projects/forgemaster/sot.git', head: '9f3c1d2aaa' })

    render(<UpdatePanel />)

    expect(screen.getByText(/En retard de 12 commits/)).toBeInTheDocument()
    expect(screen.getByText(/sot\.git/)).toBeInTheDocument()
    expect(screen.getByText(/HEAD 9f3c1d2/)).toBeInTheDocument()
    expect(screen.getByText(/site-vitrine/)).toBeInTheDocument()
  })

  it('n\'écrit JAMAIS « à jour » quand il ne peut pas comparer', () => {
    // L'invariant de tout ce cycle, appliqué à la surface : jamais de faux-vert. C'est aussi l'état NORMAL
    // d'une install publique — la majorité des instances distribuées, pas un cas de coin.
    h.version = version()   // comparable: false, aucune référence

    render(<UpdatePanel />)

    // Deux occurrences attendues : l'étiquette du badge et la phrase qui la qualifie.
    expect(screen.getAllByText(/non comparable/)).toHaveLength(2)
    // Les DEUX porteurs du verdict, chacun nié : la phrase et l'étiquette du badge. (Un `/à jour/i` nu
    // attraperait le titre du panneau, « Mise à jour » — et passerait pour une garde alors qu'il en serait
    // une autre.)
    expect(screen.queryByText(/^À jour/)).toBeNull()
    expect(screen.queryByText(/à jour · miroir/)).toBeNull()
  })

  it('nomme la référence DANS le verdict à jour — un « à jour » nu promettrait plus que mesuré', () => {
    // `stale=false` veut dire « égal au HEAD de ton miroir LOCAL », et ce miroir vieillit avec l'instance
    // (angle mort mesuré côté banc : wheel ET miroir périmés → le daemon les voit égaux). La référence
    // dans la phrase est ce qui rend le verdict jugeable ; une référence joignable est la phase 5.
    h.version = version({ comparable: true, stale: false, behind_by: 0,
                          reference: '/home/u/projects/forgemaster/sot.git', head: 'abcdef1234' })

    render(<UpdatePanel />)

    expect(screen.getByText(/À jour avec ton miroir local/)).toBeInTheDocument()
  })

  it('se tait tant que la réponse n\'est pas là', () => {
    // Contre-témoin : sans donnée, le panneau ne doit ni rassurer ni alarmer. Il continue de dire ce qu'il
    // sait (rien), et l'identité reste celle d'avant cette phase.
    h.version = null

    render(<UpdatePanel />)

    expect(screen.queryByText(/À jour|En retard|non comparable/)).toBeNull()
  })
})

describe('UpdatePanel — l\'ÉDITION : les 4 pièces, et l\'écart quand il y en a un', () => {
  // D'OÙ VIENT CE BLOC : phase 4·4. Le verdict de conformité des cartes existait — dans `forgemaster
  // toolchain check`, c'est-à-dire derrière un terminal. Une instance pouvait servir des cartes qui ne
  // sont pas celles de son édition sans qu'aucune surface ne le dise à qui n'en a pas.
  beforeEach(() => { h.runs = CALME; h.runsError = null; h.run = null; h.runError = null; h.aptitude = apte() })

  it('montre les QUATRE pièces avec leur SHA', () => {
    h.version = version({ sha: 'abcdef1234' })

    render(<UpdatePanel />)

    for (const nom of MAPS) expect(screen.getByText(nom)).toBeInTheDocument()
    // le wheel est la 4ᵉ pièce, et il est en TÊTE : la question porte sur l'ensemble, pas sur le binaire
    expect(screen.getByText('forgemaster')).toBeInTheDocument()
    expect(screen.getAllByText('a1b2c3d')).toHaveLength(3)
  })

  it('nomme la carte en écart ET le SHA que l\'édition déclare', () => {
    const base = version()
    h.version = version({
      maps: base.maps.map((m) => (m.name === 'code-map' ? { ...m, sha: SHA_B } : m)),
      edition: {
        ...base.edition, state: 'differs',
        maps: base.edition.maps.map((m) => (m.name === 'code-map'
          ? { ...m, served: SHA_B, edition: SHA_A, state: 'differs' as const } : m)),
      },
    })

    render(<UpdatePanel />)

    expect(screen.getByText(/ce n'est pas la carte que cette édition déclare/)).toBeInTheDocument()
    expect(screen.getByText(/≠ édition a1b2c3d/)).toBeInTheDocument()      // les DEUX SHA, jamais un seul
    expect(screen.getByText(/toolchain install/)).toBeInTheDocument()      // le geste est NOMMÉ
  })

  it('n\'annonce JAMAIS « conforme » sur une instance sans édition', () => {
    // Le faux-vert exact que cette sonde refuse : un checkout n'a rien à quoi se conformer, et le dire
    // n'est pas un refus. Le mode est MESURÉ, plus déduit de `sha === null`.
    h.version = version({ sha: null, install: { mode: 'checkout', reason: 'ni tampon ni édition' },
                          edition: { edition_dir: null, reason: 'pas de _maps', state: 'unknown',
                                     maps: [] } })

    render(<UpdatePanel />)

    expect(screen.getByText(/rien à quoi ses cartes se conformeraient/)).toBeInTheDocument()
    expect(screen.getByText(/checkout éditable/)).toBeInTheDocument()
    expect(screen.queryByText(/cartes conformes/)).toBeNull()
  })
})

describe('UpdatePanel — ce qui est montré quand le backend meurt en plein geste', () => {
  it("appelle ATTENDU le silence qui suit un geste, et n'affiche AUCUN échec", () => {
    // Le cœur du sujet. Le daemon qu'on vient de charger de se remplacer ne répond plus : c'est l'issue
    // nominale. Une UI naïve écrirait « erreur » ici — au moment précis où tout se passe bien.
    h.runs = { runs: [run()], total: 1, truncated: false, follow_timeout: 900 }
    h.runsError = MORT
    h.run = run()
    h.runError = MORT
    h.contact = Date.now() - 5_000

    render(<UpdatePanel />)

    expect(screen.getByRole('status')).toHaveTextContent(/attendu/)
    expect(screen.queryByText(/Le geste n'est pas parti/)).not.toBeInTheDocument()
  })

  it('ne maquille pas en bascule un daemon mort qu\'aucun geste n\'explique', () => {
    // Le contre-témoin, et il compte plus que le cas nominal : si ce silence-ci passait pour une bascule,
    // le panneau dirait « c'est normal » chaque fois que l'instance tombe.
    h.runs = { runs: [run({ state: 'done', rc: 0, verdict: 'MAJ posée' })], total: 1, truncated: false,
               follow_timeout: 900 }
    h.runsError = MORT
    h.run = run({ state: 'done', rc: 0, verdict: 'MAJ posée' })
    h.runError = MORT
    h.contact = Date.now() - 5_000

    render(<UpdatePanel />)

    expect(screen.getByRole('status')).toHaveTextContent(/injoignable/)
    expect(screen.getByRole('status')).toHaveTextContent(/n'est pas une bascule/)
  })

  it('ne qualifie aucun silence tant que l\'instance répond', () => {
    h.runs = { runs: [], total: 0, truncated: false, follow_timeout: 900 }
    h.runsError = null
    h.run = null
    h.runError = null
    h.contact = Date.now()

    render(<UpdatePanel />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByText(/Cette instance sert le build/)).toBeInTheDocument()
  })
})
