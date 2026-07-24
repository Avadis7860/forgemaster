import { Alert, Button, Card, Eyebrow } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { LAUNCH_STEPS, deriveLaunchStage } from '@/lib/launch'
import { useReconcileSocle, useRoadmapCheck } from '@/lib/queries'
import type { Roadmap } from '@/lib/schemas'

/** Frise du CYCLE DE LANCEMENT + action nommée par son RÉSULTAT. Rend lisible, dans l'UI seule (sans lire les
 *  bundles de config Claude), où en est le lancement, quelle est la prochaine action humaine, et ce qu'elle
 *  fait — le symétrique APRÈS-interview du bouton « Lancer l'interview » (avant). Ne s'affiche que pour un
 *  projet À SOCLE (interview de 1ʳᵉ session) : un projet mûr n'a pas de cycle à montrer (rend `null`).
 *
 *  L'étape courante est DÉRIVÉE de la roadmap (source unique serveur) + du gate de complétude — jamais d'état
 *  local deviné. L'action `reconcile-socle` n'apparaît qu'à l'étape « Réconciliation » (interview produite,
 *  roadmap verte, socle pas encore clôturé) et RAPPORTE ce qu'elle a fait (sha du design, N tasks, prochaine
 *  étape) — l'invalidation fait avancer la frise seule. */
export function LaunchCycle({ project, roadmap }: { project: string; roadmap: Roadmap }) {
  const check = useRoadmapCheck(project)
  const reconcile = useReconcileSocle(project)
  // Étape dérivée de la roadmap + gate (source unique serveur partagée avec l'état de fin d'interview).
  const { socle, current } = deriveLaunchStage(roadmap, check.data?.ok ?? false)
  if (!socle) return null // projet sans socle interactif → pas de cycle de lancement à rendre

  const canReconcile = current === 2
  const r = reconcile.data

  return (
    <Card className="space-y-4 p-6">
      <Eyebrow>Cycle de lancement</Eyebrow>
      {/* Chaque groupe = [flèche + étape] (flèche AVANT l'étape, sauf la 1ʳᵉ) → au wrap, la flèche part à la
          ligne AVEC l'étape qu'elle pointe (jamais orpheline en fin de ligne). Tient à 390px comme à 1400px. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {LAUNCH_STEPS.map((step, i) => (
          <div key={step.label} className="flex items-center gap-3">
            {i > 0 && (
              <span aria-hidden className="text-faint">
                →
              </span>
            )}
            <Step
              n={String(i + 1)}
              label={step.label}
              state={i < current ? 'done' : i === current ? 'active' : 'pending'}
            />
          </div>
        ))}
      </div>

      {/* Prochaine action humaine de l'étape courante — répond « quoi faire maintenant » même hors du gate de
          réconciliation (où le bouton primaire ci-dessous prend le relais). Ligne de texte (tissu, pas panneau). */}
      {!canReconcile && (
        <p className="text-sm text-fg">
          <span className="text-muted">Prochaine action : </span>
          {LAUNCH_STEPS[current].next}
        </p>
      )}
      <p className="text-xs text-faint">
        Le socle est le design de ton projet. Une fois l'interview finie, la forge le valide (réconciliation)
        puis lance les features de travail — tu n'as pas besoin de connaître « cockpit run ».
      </p>

      {canReconcile && (
        <div>
          <Button variant="primary" busy={reconcile.isPending} onClick={() => reconcile.mutate()}>
            Valider l'interview & clôturer le socle
          </Button>
        </div>
      )}

      {r && (
        <Alert
          tone={r.status === 'reconciled' ? 'ok' : 'info'}
          title={r.status === 'reconciled' ? 'Socle réconcilié' : 'Rien à réconcilier'}
        >
          {r.status === 'reconciled' ? (
            <>
              Design committé
              {r.design_sha ? ` (${r.design_sha.slice(0, 8)})` : ''} · {r.socle_tasks_closed} task
              {r.socle_tasks_closed > 1 ? 's' : ''} socle → done. Prochaine étape : {r.next_step}
            </>
          ) : (
            r.next_step
          )}
        </Alert>
      )}
      {reconcile.isError && (
        <Alert tone="danger" title="Échec de la réconciliation">
          {reconcile.error instanceof ApiError ? reconcile.error.detail : String(reconcile.error)}
        </Alert>
      )}
    </Card>
  )
}

/** Puce d'étape de la frise : cercle numéroté + libellé. `done` = ✓ vert, `active` = accent cuivre, `pending`
 *  = neutre en retrait. Relief par valeur de token (pas de panneau décoratif) — même grammaire que le stepper
 *  Travail (fabric, pas carte-dans-carte). */
function Step({ n, label, state }: { n: string; label: string; state: 'done' | 'active' | 'pending' }) {
  const circle =
    state === 'done'
      ? 'bg-ok-500/20 text-ok-500'
      : state === 'active'
        ? 'bg-accent-600 text-on-accent'
        : 'border border-border-strong bg-surface-raised text-muted'
  return (
    <span className="flex items-center gap-2 text-sm">
      <span className={`grid h-5 w-5 place-items-center rounded-full text-xs font-medium ${circle}`}>
        {state === 'done' ? '✓' : n}
      </span>
      <span className={state === 'pending' ? 'text-muted' : 'font-medium text-fg'}>{label}</span>
    </span>
  )
}
