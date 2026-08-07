import { Badge, Button, Collapsible } from '@/components/ui'
import { UPDATE_RUN_TONE, toneFor } from '@/lib/statusTone'
import type { UpdateRun } from '@/lib/schemas'

const ETIQUETTE: Record<string, string> = {
  failed: 'échouée',
  running: 'en cours',
  unknown: 'sans nouvelle',
  interrupted: 'interrompue',
  never_started: 'jamais partie',
}

/** Le libellé porte le SENS DE MARCHE et l'état d'un coup — « MAJ posée », « retour effectué », « MAJ
 *  interrompue ». Les séparer obligerait à écrire le mot « mise à jour » juste sous le titre de la section,
 *  qui le dit déjà : la même phrase deux fois n'informe personne. */
function etiquette(state: string, mode: string | null): string {
  const geste = mode === 'rollback' ? 'retour' : 'MAJ'
  if (state === 'done') return mode === 'rollback' ? 'retour effectué' : 'MAJ posée'
  return `${geste} ${ETIQUETTE[state] ?? state}`
}

/** Le geste en cours, ou le dernier verdict — **relu du disque** à chaque battement, jamais d'une mémoire
 *  de ce navigateur.
 *
 *  Deux phrases, et elles ne se déduisent PAS l'une de l'autre : le **verdict** dit ce qui s'est passé,
 *  l'**impact** dit jusqu'où ça a été. « MAJ refusée — le vivant ne sert pas » ne renseigne pas sur l'état
 *  du service ; « aucun : le service n'a pas été touché » si. C'est exactement la question de quelqu'un qui
 *  n'a pas de terminal pour aller voir, donc la seconde phrase s'affiche dès qu'elle existe — et **rien**
 *  ne s'affiche quand elle vaut `null`, parce qu'un `impact` absent veut dire « je n'en sais rien », pas
 *  « rien n'a bougé ». */
export function RunFollow({ run }: { run: UpdateRun }) {
  const attend = run.state === 'running' || run.state === 'unknown'
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-fg">{attend ? 'Geste en cours' : 'Dernier geste'}</span>
        <Badge tone={toneFor(UPDATE_RUN_TONE, run.state)} dot>
          {etiquette(run.state, run.mode)}
        </Badge>
        <span className="font-mono text-xs text-faint">{run.run}</span>
      </div>

      {run.verdict && <p className="text-sm text-muted">{run.verdict}</p>}
      {run.impact && (
        <p className="text-sm text-muted">
          <span className="text-xs uppercase tracking-wide text-faint">Ce qui a bougé — </span>
          {run.impact}
        </p>
      )}
      {run.target && (
        <p className="break-all font-mono text-xs text-faint">{run.target}</p>
      )}

      {/* Le journal est secondaire : il se déplie, il ne s'impose pas. `variant='section'` — pas de cadre
          dans un bloc qui en a déjà un (règle tissu > panneau). */}
      {run.journal && (
        <Collapsible title="Journal de l'applicateur" variant="section">
          <pre className="max-h-72 overflow-auto rounded bg-faint/5 p-3 font-mono text-xs leading-relaxed text-muted">
            {run.journal}
          </pre>
        </Collapsible>
      )}
    </div>
  )
}

/** Une ligne d'historique : rangée, jamais une carte — un item mono-contenu entouré d'un cadre est du
 *  chrome net, zéro information ajoutée. */
export function RunRow({ run, actif, onChoisir }: {
  run: UpdateRun
  actif: boolean
  onChoisir: () => void
}) {
  return (
    <li>
      {/* La sélection passe par la VARIANTE de la primitive, pas par des classes ajoutées : `cn` est un
          simple `join`, donc deux utilitaires de la même propriété (`bg-transparent` du ghost contre un
          fond actif) se départageraient par l'ordre de la feuille de style — un pile ou face. */}
      <Button
        variant={actif ? 'secondary' : 'ghost'}
        size="sm"
        onClick={onChoisir}
        aria-current={actif || undefined}
        className="h-auto w-full justify-start gap-2 py-1.5"
      >
        <Badge tone={toneFor(UPDATE_RUN_TONE, run.state)} dot>
          {etiquette(run.state, run.mode)}
        </Badge>
        <span className="font-mono text-xs text-muted">{run.run}</span>
      </Button>
    </li>
  )
}
