import { describe, expect, it } from 'vitest'
import { edition } from './instanceEdition'
import { VersionSchema } from './schemas'
import type { Version } from './schemas'

const SHA_A = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2'
const SHA_B = '9f8e7d6c5b4a9f8e7d6c5b4a9f8e7d6c5b4a9f8e'
const NOMS = ['code-map', 'docs-map', 'front-map']

function v(over: Partial<Version> = {}): Version {
  return {
    version: '0.1.0', sha: 'ab12345def', committed_at: '2026-08-08T00:00:00Z',
    comparable: false, stale: null, behind_by: null, missing_types: [],
    reference: null, head: null,
    install: { mode: 'edition', reason: null },
    maps: NOMS.map((name) => ({
      name, sha: SHA_A, requested_ref: null, source: 'edition' as const, reason: null,
    })),
    edition: {
      edition_dir: '/opt/fm/venv/lib/python3.12/site-packages/forgemaster/_maps', reason: null,
      state: 'up-to-date',
      maps: NOMS.map((name) => ({
        name, served: SHA_A, edition: SHA_A, state: 'up-to-date' as const, reason: null,
      })),
    },
    mcp: { topology: 'none', sha: null, endpoint: null, reason: 'aucun endpoint MCP configuré' },
    // Le volet CANAL — muet par défaut dans ces décors : `never` est l'état honnête d'une instance
    // qui n'a jamais interrogé, et c'est celui qui laisse le miroir local parler comme avant.
    channel: { state: 'never', reason: '', from_attempt: true, verified_at: null, announced: null,
               attempt: { state: 'never', at: null, reason: '' } },
    ...over,
  }
}

describe('edition — « quelle édition tourne ici ? », et jamais un vert par défaut', () => {
  it('rend les QUATRE pièces, wheel en tête', () => {
    // La question porte sur l'ENSEMBLE, pas sur le seul binaire qui répond. Le wheel n'a pas d'état de
    // conformité : il EST l'édition, il ne peut pas différer de lui-même — lui en inventer un le ferait
    // passer pour une pièce qu'on aurait oublié de vérifier.
    const e = edition(v())
    expect(e.pieces.map((p) => p.nom)).toEqual(['forgemaster', ...NOMS])
    expect(e.pieces[0].etat).toBeNull()
    expect(e.pieces.slice(1).every((p) => p.etat === 'up-to-date')).toBe(true)
    expect(e.etat).toBe('conforme')
    expect(e.remede).toBeNull()                       // rien à réparer ⇒ aucune commande décorative
  })

  it('nomme la carte en écart, les DEUX SHA et le geste qui la repose', () => {
    const base = v()
    const e = edition(v({
      maps: base.maps.map((m) => (m.name === 'code-map' ? { ...m, sha: SHA_B } : m)),
      edition: {
        ...base.edition, state: 'differs',
        maps: base.edition.maps.map((m) => (m.name === 'code-map'
          ? { ...m, served: SHA_B, edition: SHA_A, state: 'differs' as const } : m)),
      },
    }))
    expect(e.etat).toBe('derive')
    expect(e.titre).toMatch(/code-map/)
    const carte = e.pieces.find((p) => p.nom === 'code-map')!
    expect(carte.sha).toBe(SHA_B)                     // ce qui est SERVI
    expect(carte.attendu).toBe(SHA_A)                 // ce que l'édition DÉCLARE — les deux, jamais un seul
    expect(e.remede).toMatch(/toolchain install/)
  })

  it('n\'appelle JAMAIS « conforme » une conformité qu\'il n\'a pas pu vérifier', () => {
    // Le piège de cette surface : le seul état confortable est le seul qu'on n'a pas le droit d'écrire par
    // défaut. `unknown` vaut pour une carte non installée comme pour une édition amputée — deux cas où le
    // vert serait un mensonge exact, pas une approximation.
    const base = v()
    const e = edition(v({
      edition: {
        ...base.edition, state: 'unknown',
        maps: base.edition.maps.map((m) => (m.name === 'front-map'
          ? { ...m, served: null, edition: SHA_A, state: 'unknown' as const,
              reason: '`front-map` n\'est pas installée dans le venv d\'outils' } : m)),
      },
    }))
    expect(e.etat).toBe('non-verifiee')
    expect(e.titre).toMatch(/n'est pas « conforme »/)
    expect(e.pieces.find((p) => p.nom === 'front-map')!.etat).toBe('unknown')
  })

  it('distingue un checkout (rien à comparer) d\'un wheel qui a PERDU son édition', () => {
    // LE cas qui justifie le champ `install`. Les deux n'ont pas d'édition lisible — mais l'un est le mode
    // de développement (normal), l'autre est un artefact de release dégradé (à réparer). Les confondre
    // ferait passer un défaut d'artefact pour un choix de développeur.
    const vide = { edition_dir: null, reason: 'l\'édition installée ne porte pas les cartes', maps: [],
                   state: 'unknown' as const }
    const dev = edition(v({ sha: null, install: { mode: 'checkout', reason: 'ni tampon ni édition' },
                            edition: vide }))
    const degrade = edition(v({ install: { mode: 'wheel', reason: 'wheel bâti SANS son édition' },
                                edition: vide }))
    expect(dev.etat).toBe('sans-edition')
    expect(dev.ton).toBe('info')                      // un checkout n'est pas une panne
    expect(degrade.etat).toBe('non-verifiee')
    expect(degrade.ton).toBe('warn')                  // un wheel sans son édition, si
    expect(dev.titre).not.toBe(degrade.titre)
  })

  it('ne dit pas « au moins une » quand il n\'y en a AUCUNE', () => {
    // Outillage illisible : le backend rend une liste vide et pose sa raison. Annoncer une comparaison
    // partielle sur zéro carte affichée ferait chercher laquelle a dérivé.
    const base = v()
    const e = edition(v({
      maps: [],
      edition: { ...base.edition, state: 'unknown', maps: [],
                 reason: 'l\'outillage de cet hôte n\'est pas lisible' },
    }))
    expect(e.etat).toBe('non-verifiee')
    expect(e.titre).toMatch(/Aucune carte n'a pu être lue/)
    expect(e.detail).toMatch(/outillage/)
    expect(e.pieces).toHaveLength(1)                  // le wheel seul, et il ne prétend rien de plus
  })

  it('donne à chaque pièce sans SHA son MOTIF, jamais un tiret muet', () => {
    const base = v()
    const e = edition(v({
      maps: base.maps.map((m) => (m.name === 'docs-map'
        ? { ...m, sha: null, source: 'unknown' as const,
            reason: '`docs-map` n\'est pas installée dans le venv d\'outils' } : m)),
    }))
    const carte = e.pieces.find((p) => p.nom === 'docs-map')!
    expect(carte.sha).toBeNull()
    expect(carte.motif).toMatch(/venv d'outils/)
  })
})

describe('VersionSchema — le contrat ne peut pas glisser en silence', () => {
  // Zod strippe les clés INCONNUES sans un mot : c'est ainsi que `maps` et `mcp` sont arrivés au navigateur
  // et ont été jetés pendant des semaines. La garde symétrique est ici — un volet qui DISPARAÎT du payload
  // doit rougir à la table, pas se remarquer sur une surface vide.
  it.each(['install', 'maps', 'edition', 'mcp'])('rougit si le volet %s disparaît', (volet) => {
    const payload: Record<string, unknown> = { ...v() }
    delete payload[volet]
    expect(VersionSchema.safeParse(payload).success).toBe(false)
  })

  it('accepte le payload complet de la route', () => {
    expect(VersionSchema.safeParse(v()).success).toBe(true)
  })
})
