import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'

const h = vi.hoisted(() => ({ plan: null as unknown, erreur: null as unknown, pending: false }))

vi.mock('@/lib/queries', () => ({
  useUpdatePlan: () => ({
    data: h.plan, error: h.erreur, isError: h.erreur !== null, isPending: h.pending,
  }),
}))

const { UpdatePreview } = await import('./UpdatePreview')

const REFUS =
  "un dispatch tourne encore sur atlas — la MAJ arrêterait le service et l'emporterait avec lui.\n" +
  '      → attends sa fin, ou arrête-le'

describe('UpdatePreview — la prévisualisation EST le consentement', () => {
  it('rend les lignes du daemon telles quelles, avec le geste armé', () => {
    h.plan = { mode: 'apply', scope: 'user', describe: ['== ce qui va se passer', '   venv neuf'], plan: {} }
    h.erreur = null
    render(<UpdatePreview mode="apply" cible="/tmp/x.whl" onLancer={() => {}} enCours={false} />)
    expect(screen.getByText(/ce qui va se passer/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Mettre à jour/ })).toBeInTheDocument()
  })

  it('affiche un refus INTÉGRAL et DÉSARME le geste', () => {
    // Un 409 est une réponse, pas une panne : le daemon refuse dans son état et dit pourquoi, en entier.
    // Laisser le bouton armé sous ce texte inviterait à forcer une porte qu'il vient de fermer.
    h.plan = null
    h.erreur = new ApiError(409, REFUS)
    render(<UpdatePreview mode="apply" cible="/tmp/x.whl" onLancer={() => {}} enCours={false} />)
    expect(screen.getByText(/un dispatch tourne encore sur atlas/)).toBeInTheDocument()
    expect(screen.getByText(/attends sa fin/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Mettre à jour/ })).not.toBeInTheDocument()
  })

  it('ne demande MÊME PAS le plan quand la page sait déjà qu\'un geste est en vol', () => {
    // Le serveur refuserait de toute façon (409). Ce que ce chemin évite, c'est d'armer une action qu'on
    // SAIT morte le temps d'un aller-retour — et de faire clignoter un bouton entre l'ouverture et le refus.
    h.plan = { mode: 'apply', scope: 'user', describe: ['== ce qui va se passer'], plan: {} }
    h.erreur = null
    render(<UpdatePreview mode="apply" cible="/tmp/x.whl" onLancer={() => {}} enCours={false}
                          bloque="Un geste de mise à jour est en vol (MAJ 2026-08-07T12-04-38Z)." />)
    expect(screen.getByText(/est en vol \(MAJ 2026-08-07T12-04-38Z\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Mettre à jour/ })).not.toBeInTheDocument()
    // Le plan est là dans le double, et il ne doit PAS s'afficher : c'est ce qui prouve que la requête n'a
    // pas été faite pour rien — le composant se coupe AVANT de la consommer.
    expect(screen.queryByText(/ce qui va se passer/)).not.toBeInTheDocument()
  })
})
