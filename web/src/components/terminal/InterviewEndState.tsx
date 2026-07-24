import { useNavigate } from '@tanstack/react-router'
import { Alert, Button } from '@/components/ui'
import { deriveLaunchStage } from '@/lib/launch'
import { useRoadmap, useRoadmapCheck } from '@/lib/queries'

/** État de FIN du terminal d'INTERVIEW (flavor `interview`, socket fermé). Le PTY `cockpit interview` a quitté
 *  (`exec` → EOF → fermeture PROPRE, ce n'est pas un crash) : au lieu du « fermé » shell générique + « Relancer »
 *  trompeur, on lit la roadmap (source unique serveur, MÊME dérivation que la frise de lancement `LaunchCycle`)
 *  pour dire honnêtement où en est le socle et OUVRIR le chemin suivant — au lieu de laisser l'humain devant un
 *  terminal gelé sans issue.
 *
 *  Deux issues, jamais un cul-de-sac :
 *   - interview INCOMPLÈTE (pas de roadmap valide) → « Reprendre l'interview » (reconnexion, là « Relancer » a du
 *     sens) ;
 *   - interview PRODUCTIVE (design prêt / socle clos) → « Continuer le lancement » vers l'Accueil, où la frise
 *     enchaîne (réconciliation puis drain des features) — le chemin que le log CLI annonce, rendu CLIQUABLE. */
export function InterviewEndState({ project, onReconnect }: { project: string; onReconnect: () => void }) {
  const roadmap = useRoadmap(project)
  const check = useRoadmapCheck(project)
  const navigate = useNavigate()

  // Pas encore de vérité serveur → on n'affirme rien (le terminal reste tel quel, pas de faux succès/échec).
  if (!roadmap.data) return null
  const stage = deriveLaunchStage(roadmap.data, check.data?.ok ?? false)
  // Projet SANS socle interactif (mûr) → pas de sémantique d'interview à rendre : fallback générique de fermeture.
  if (!stage.socle) return null

  // Productive = roadmap valide + features de travail authorées (étape ≥ Réconciliation). En-deçà, l'interview
  // s'est fermée sans produire de socle exploitable → reprise honnête.
  const productive = stage.current >= 2

  if (!productive) {
    return (
      <Alert tone="warn" title="Interview incomplète">
        L'interview s'est fermée sans que le socle soit prêt (roadmap de travail non produite). Reprends-la pour la
        terminer — ou consulte le log de session ci-dessous si elle s'est interrompue.
        <div className="mt-3">
          <Button variant="primary" size="sm" onClick={onReconnect}>
            Reprendre l'interview
          </Button>
        </div>
      </Alert>
    )
  }

  const closed = stage.socleClosed
  return (
    <Alert tone="ok" title={closed ? 'Interview terminée · socle clos' : 'Interview terminée · design prêt'}>
      {closed
        ? 'Le socle de conception est clôturé. Lance le travail : la frise de lancement enchaîne le drain des features.'
        : "Le design est prêt. Valide l'interview pour clôturer le socle, puis draine les features de travail — tout depuis la frise de lancement."}
      <div className="mt-3">
        <Button
          variant="primary"
          size="sm"
          onClick={() => navigate({ to: '/$project', params: { project } })}
        >
          Continuer le lancement
        </Button>
      </div>
    </Alert>
  )
}
