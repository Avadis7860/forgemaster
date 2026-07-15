import { Suspense, lazy } from 'react'
import { Link, useParams } from '@tanstack/react-router'
import { Alert, Badge, Button, Card, Eyebrow, LoadingState } from '@/components/ui'
import { useDeployments, useDocs, useRoadmap } from '@/lib/queries'

// react-markdown est lourd → code-split : le renderer ne se charge QUE quand la carte docs a du contenu.
const DocView = lazy(() => import('@/components/docs/DocView').then((m) => ({ default: m.DocView })))

/** Accueil du workspace projet (P4, route index) : la page d'ENTRÉE — « ce que c'est » (Docs FONDU ici, l'onglet
 *  Docs a disparu) + les points d'entrée à *scent* vers les 3 vraies surfaces (Roadmap · Travail · Ops). Chaque
 *  tuile porte un badge d'état dérivé des queries DÉJÀ servies (roadmap · déploiements) — aucun endpoint neuf. Le
 *  badge se remplit quand sa query est prête ; la tuile s'affiche tout de suite (scent secondaire non bloquant). */
export function AccueilTab() {
  const project = useParams({ strict: false }).project ?? ''
  const docs = useDocs(project)
  const roadmap = useRoadmap(project)
  const deployments = useDeployments(project)

  const features = roadmap.data?.features ?? []
  const nFeatures = features.length
  const nNext = features.filter((f) => f.next).length
  const hasWorktree = features.some((f) => f.worktree_path != null)
  const nDeploy = (deployments.data?.deployments ?? []).filter((d) => d.status !== 'no_deploy').length

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-6 py-8">
      {/* En-tête léger : l'identité (nom/backend/sot_path) vit déjà dans le header du workspace ; ici, juste
          l'orientation + UNE action primaire qui ramène à la boucle de travail. */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <Eyebrow>Projet</Eyebrow>
          <h2 className="text-lg font-semibold text-fg">Vue d'ensemble</h2>
        </div>
        <Link to="/$project/travail" params={{ project }}>
          <Button variant="primary">Reprendre le travail →</Button>
        </Link>
      </header>

      {/* « Ce que c'est » = la carte docs FONDUE (ex-onglet Docs). Lue depuis le repo (SoT-and-derive) ; 4 états. */}
      <Card className="space-y-4 p-6">
        <Eyebrow>Documentation · {project}</Eyebrow>
        {docs.isLoading ? (
          <LoadingState label="Chargement de la doc…" />
        ) : docs.isError ? (
          <Alert tone="danger" title="Docs indisponibles">{docs.error.message}</Alert>
        ) : !docs.data?.found ? (
          <p className="text-sm text-muted">
            Ce dépôt n'a pas de <code className="text-xs">docs/tool-card.md</code> (ni de{' '}
            <code className="text-xs">README.md</code>). Ajoute-la dans le repo — le cockpit la relira.
          </p>
        ) : (
          <>
            {docs.data.truncated && (
              <Alert tone="info" title="Doc tronquée">Le fichier dépasse la taille d'affichage — contenu coupé.</Alert>
            )}
            <Suspense fallback={<LoadingState label="Rendu de la doc…" />}>
              <DocView content={docs.data.content} />
            </Suspense>
          </>
        )}
      </Card>

      {/* Points d'entrée à scent : où aller ensuite, avec un aperçu d'état (badge dérivé, pas d'endpoint neuf). */}
      <section className="space-y-3">
        <Eyebrow>Où aller</Eyebrow>
        <div className="grid gap-3 sm:grid-cols-3">
          <ScentTile
            to="/$project/roadmap" project={project}
            label="Roadmap" hint="Le DAG des features × tasks"
            badge={
              roadmap.data && (
                <Badge tone={nNext > 0 ? 'accent' : 'neutral'} dot>
                  {nFeatures} feature{nFeatures > 1 ? 's' : ''}{nNext > 0 ? ` · ${nNext} NEXT` : ''}
                </Badge>
              )
            }
          />
          <ScentTile
            to="/$project/travail" project={project}
            label="Travail" hint="Dispatch → validation → merge"
            badge={
              roadmap.data && (
                <Badge tone={hasWorktree ? 'warn' : 'neutral'} dot>
                  {hasWorktree ? 'branche à valider' : 'rien en cours'}
                </Badge>
              )
            }
          />
          <ScentTile
            to="/$project/ops" project={project}
            label="Ops" hint="Terminal · runtime · git · flow"
            badge={
              deployments.data && (
                <Badge tone={nDeploy > 0 ? 'accent' : 'neutral'} dot>
                  {nDeploy} déploiement{nDeploy > 1 ? 's' : ''}
                </Badge>
              )
            }
          />
        </div>
      </section>
    </div>
  )
}

/** Une tuile-lien vers une surface, stylée comme une carte (tokens de la charte) — navigation, pas action, donc
 *  un `<Link>` porte la recette de carte directement (pas un `<button>`). Le badge d'état est optionnel. */
function ScentTile({
  to, project, label, hint, badge,
}: {
  to: '/$project/roadmap' | '/$project/travail' | '/$project/ops'
  project: string
  label: string
  hint: string
  badge?: React.ReactNode
}) {
  return (
    <Link
      to={to}
      params={{ project }}
      className="group flex flex-col gap-2 rounded-card border border-border bg-surface p-4 transition-colors hover:border-border-strong"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-fg">{label}</span>
        <span aria-hidden className="text-muted transition-transform group-hover:translate-x-0.5">→</span>
      </div>
      <p className="text-xs text-muted">{hint}</p>
      {badge}
    </Link>
  )
}
