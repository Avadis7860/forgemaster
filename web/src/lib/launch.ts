import type { FeatureWithTasks, Roadmap } from '@/lib/schemas'

// Étapes du CYCLE DE LANCEMENT (labels d'état NEUTRES : l'état vient du rendu ✓/actif/pending, jamais du temps
// du verbe) + la PROCHAINE ACTION humaine de chaque étape. SOURCE UNIQUE partagée par la frise (`LaunchCycle`,
// Accueil) et l'état de fin du terminal d'interview (`InterviewEndState`, Ops) — même dérivation, un seul foyer,
// pas de logique de socle dupliquée (frontière de confiance : un seuil ne se ré-implémente pas).
export const LAUNCH_STEPS = [
  { label: 'Interview', next: "Mène l'interview de conception dans l'onglet Ops (terminal)." },
  { label: 'Design', next: "Termine l'interview jusqu'à une roadmap valide (design rempli + features)." },
  { label: 'Réconciliation', next: "Valide l'interview ci-dessous pour clôturer le socle." },
  { label: 'Socle mergé', next: 'Lance le travail — le socle se merge au premier drain.' },
  { label: 'Features de travail', next: 'Le socle est lancé — draine les features de travail.' },
] as const

export type LaunchStage = {
  socle: FeatureWithTasks | undefined // la feature socle (interactive) si le projet en a une
  hasWork: boolean
  checkGreen: boolean
  socleClosed: boolean
  socleMerged: boolean
  current: number // index d'étape 0..4 (LAUNCH_STEPS), ou -1 si projet sans socle interactif
}

/** Dérive l'étape courante du cycle de lancement depuis la roadmap (source unique serveur) + le gate de
 *  complétude — jamais d'état local deviné. Chaque seuil est un FAIT serveur : `hasWork` (≥1 feature de travail
 *  authorée), `checkGreen` (roadmap valide), `socleClosed` (toutes les tasks socle done/cancelled), `socleMerged`
 *  (le socle a été mergé au 1ᵉ drain). Index : Interview(0) → Design(1) → Réconciliation(2) → Socle mergé(3) →
 *  Features de travail(4). Un projet SANS socle interactif renvoie `current: -1` (rien à lancer). */
export function deriveLaunchStage(roadmap: Roadmap, checkOk: boolean): LaunchStage {
  const socle = roadmap.features.find((f) => f.tasks.some((t) => t.mode === 'interactive'))
  if (!socle)
    return { socle: undefined, hasWork: false, checkGreen: false, socleClosed: false, socleMerged: false, current: -1 }
  const hasWork = roadmap.features.some((f) => f.slug !== socle.slug)
  const checkGreen = checkOk
  const socleClosed = socle.tasks.every((t) => t.status === 'done' || t.status === 'cancelled')
  const socleMerged = socle.status === 'merged'
  const current = !hasWork ? 0 : !checkGreen ? 1 : !socleClosed ? 2 : !socleMerged ? 3 : 4
  return { socle, hasWork, checkGreen, socleClosed, socleMerged, current }
}
