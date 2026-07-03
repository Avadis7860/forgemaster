// schemas — contrat typé du daemon (SoT côté front). Zod = validation runtime + inférence des types.
// Portée V1→V2 : entités projet/feature/task/roadmap/next. Job/Gate/Merge seront ajoutés dans leurs
// vagues (V3/V4) pour n'avoir aucun schéma non consommé. Miroir exact du contrat exposé par le daemon.
import { z } from 'zod'

export const HealthSchema = z.object({ status: z.string(), version: z.string() })
export type Health = z.infer<typeof HealthSchema>

export const ProjectSchema = z.object({
  id: z.string(),
  slug: z.string(),
  name: z.string().nullable(),
  sot_path: z.string(),
  mirror_remote: z.string().nullable(),
  backend: z.string(),
  kind: z.string(),                 // 'project' | 'tool' (v3) — classification, pilote le rail 2 sections
  owner: z.string().nullable(),     // compat multi-utilisateur (v3, nullable en mono-user)
  credential_ref: z.string().nullable(),  // réf opaque vers le token du store (v4) — jamais le token
  created_at: z.string(),
})
export type Project = z.infer<typeof ProjectSchema>

export const FeatureSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  slug: z.string(),
  title: z.string().nullable(),
  branch: z.string(),
  worktree_path: z.string().nullable(),
  status: z.string(),
  created_at: z.string(),
})
export type Feature = z.infer<typeof FeatureSchema>

// depends_on arrive DÉJÀ dé-sérialisé en liste par le daemon (model.list_tasks).
export const TaskSchema = z.object({
  id: z.string(),
  feature_id: z.string(),
  slug: z.string(),
  title: z.string().nullable(),
  status: z.string(),
  depends_on: z.array(z.string()),
  priority: z.string(),
  created_at: z.string(),
})
export type Task = z.infer<typeof TaskSchema>

// La task « classée » par le résolveur : task + état DAG + raisons de blocage.
export const TaskClassifiedSchema = TaskSchema.extend({
  state: z.string(),
  blockers: z.array(z.string()),
})
export type TaskClassified = z.infer<typeof TaskClassifiedSchema>

// La roadmap est CLASSÉE côté daemon : tasks avec état DAG (resolver.classify) + NEXT dispatchable
// de la feature (slug, ou null si aucune READY). Le front ne recalcule jamais l'état (source unique Python).
export const FeatureWithTasksSchema = FeatureSchema.extend({
  tasks: z.array(TaskClassifiedSchema),
  next: z.string().nullable(),
})
export type FeatureWithTasks = z.infer<typeof FeatureWithTasksSchema>

export const RoadmapSchema = z.object({
  project: z.string(),
  features: z.array(FeatureWithTasksSchema),
})
export type Roadmap = z.infer<typeof RoadmapSchema>

export const NextSchema = z.object({
  next: TaskClassifiedSchema.nullable(),
  n_tasks: z.number(),
})
export type Next = z.infer<typeof NextSchema>

export const ProjectsListSchema = z.object({ projects: z.array(ProjectSchema) })

export interface CreateProjectInput {
  slug: string
  name?: string | null
  mirror_remote?: string | null
  kind?: string             // 'project' (défaut) | 'tool'
}

// -- V3 dispatch : job (run worker) + rapport de dispatch + événements de transcript (streamés en WS) --

// Une ligne de `dispatch_jobs` enrichie du `task_slug` (join list_jobs). Champs consommés déclarés ;
// les colonnes non listées sont ignorées par Zod (pas de bruit côté front).
export const JobSchema = z.object({
  id: z.string(),
  task_slug: z.string().nullish(),
  status: z.string(),
  num_turns: z.number().nullish(),
  cost_usd: z.number().nullish(),
  wall_s: z.number().nullish(),
  started_at: z.string().nullish(),
  ended_at: z.string().nullish(),
})
export type Job = z.infer<typeof JobSchema>

export const JobsListSchema = z.object({ jobs: z.array(JobSchema) })

// Rapport du POST dispatch (worker.dispatch_next) : bloquant, rendu à la FIN du run.
export const DispatchReportSchema = z.object({
  dispatched: z.boolean(),
  reason: z.string(),
  task: z.string().nullish(),
  job_id: z.string().nullish(),
})
export type DispatchReport = z.infer<typeof DispatchReportSchema>

// Événement de transcript normalisé (jobs.normalize_line) poussé par le WS. `job` = frame terminale
// synthétique (fin de run) émise par stream.stream_job. Le front ne fabrique jamais ces formes.
const AssistantToolSchema = z.object({ name: z.string().nullable(), input_summary: z.string() })
const ToolResultItemSchema = z.object({ ok: z.boolean(), summary: z.string() })

export const TranscriptEventSchema = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('assistant'),
    ts: z.string().nullish(),
    text: z.string().optional(),
    tools: z.array(AssistantToolSchema).optional(),
    usage: z
      .object({ output_tokens: z.number().optional(), web_search_requests: z.number().optional() })
      .optional(),
  }),
  z.object({
    type: z.literal('tool_result'),
    ts: z.string().nullish(),
    results: z.array(ToolResultItemSchema),
  }),
  z.object({
    type: z.literal('job'),
    status: z.string(),
    num_turns: z.number().nullish(),
    cost_usd: z.number().nullish(),
    wall_s: z.number().nullish(),
  }),
])
export type TranscriptEvent = z.infer<typeof TranscriptEventSchema>
export type JobFrame = Extract<TranscriptEvent, { type: 'job' }>

// Détail d'un job (GET /api/jobs/{id}) : la ligne job + son transcript normalisé lu one-shot (jobs.tail).
// Sert la vue AT-REST d'un run terminé (pas de socket ouvert) ; le WS ne streame que le live d'un run en cours.
export const JobDetailSchema = z.object({ job: JobSchema, events: z.array(TranscriptEventSchema) })
export type JobDetail = z.infer<typeof JobDetailSchema>

// -- V4 gate & merge : statut brut des gates (review Tier-1 + verify Tier-1.5) + décision composée --------

// Counts de sévérité reviewer (🔴 red / 🟡 yellow / 🟣 purple) — null si aucun verdict.
export const GateCountsSchema = z.object({ red: z.number(), yellow: z.number(), purple: z.number() })

// Statut du verdict Tier-1 (review.status) : présent / frais (reviewed_sha == HEAD) / bloquant (≥1 🔴).
export const ReviewStatusSchema = z.object({
  present: z.boolean(),
  fresh: z.boolean(),
  blocking: z.boolean(),
  counts: GateCountsSchema.nullish(),
  reviewed_sha: z.string().nullish(),
})
export type ReviewStatus = z.infer<typeof ReviewStatusSchema>

// Statut du verdict Tier-1.5 (verify.status) : rendu prouvé (feature-verified). N/A hors surface UI.
export const VerifyStatusSchema = z.object({
  present: z.boolean(),
  fresh: z.boolean(),
  ok: z.boolean().nullish(),
  blocking: z.boolean(),
  n_targets: z.number().nullish(),
  n_failed: z.number().nullish(),
  reviewed_sha: z.string().nullish(),
})
export type VerifyStatus = z.infer<typeof VerifyStatusSchema>

// La décision composée (compose_merge_decision) : le front ne la RECALCULE jamais (source unique Python).
// `decision` = 'hold' | 'merge' ; gate vert SANS go ⇒ hold ; overrides tracés séparément.
export const MergeDecisionSchema = z.object({
  allow: z.boolean(),
  decision: z.string(),
  gate_green: z.boolean(),
  human_go: z.boolean(),
  ui_touched: z.boolean(),
  t15_overridden: z.boolean(),
  t1_overridden: z.boolean(),
  blockers: z.array(z.string()),
  reasons: z.array(z.string()),
})
export type MergeDecision = z.infer<typeof MergeDecisionSchema>

// GET /api/gate/{p}/{f} : statut brut + décision en preview GO=false (hold). `decision`=null si la feature
// n'a pas de branche (jamais dispatchée). Une seule lecture idempotente sert toute la vue Gate.
export const GateStatusSchema = z.object({
  head_sha: z.string().nullish(),
  ui_touched: z.boolean(),
  review: ReviewStatusSchema,
  verify: VerifyStatusSchema,
  decision: MergeDecisionSchema.nullish(),
})
export type GateStatus = z.infer<typeof GateStatusSchema>

// POST /api/merge/{p}/{f} : rapport de merge sous GO humain. `merged` prouve la mutation ; `decision`
// reflète l'évaluation (même forme que le preview). `merge_sha` = SHA promu sur dev/main si mergé.
export const MergeReportSchema = z.object({
  merged: z.boolean(),
  allow: z.boolean(),
  decision: MergeDecisionSchema.nullish(),
  feature: z.string(),
  head_sha: z.string().nullish(),
  merge_sha: z.string().nullish(),
  closed_tasks: z.array(z.string()),
  pending_tasks: z.array(z.string()),
  reason: z.string(),
})
export type MergeReport = z.infer<typeof MergeReportSchema>

export interface MergeInput {
  go: boolean
  t1_override?: string
  t15_override?: string
}

// -- Vue Git (read-only) : branches + avance/retard main↔dev + log par réf ------------------------

// Une entrée de `git log --oneline` (parser pur Python) : sha court + sujet.
export const GitLogEntrySchema = z.object({ sha: z.string(), subject: z.string() })
export type GitLogEntry = z.infer<typeof GitLogEntrySchema>

// Une branche locale du SoT bare (for-each-ref) : nom + sha court + sujet du commit de tête.
export const GitBranchSchema = z.object({ name: z.string(), sha: z.string(), subject: z.string() })
export type GitBranch = z.infer<typeof GitBranchSchema>

// Avance/retard de `head` vs `base` : `ahead` = commits de head absents de base ; `behind` l'inverse.
// En sot:local (main-suit-dev), base=main head=dev ⇒ `ahead` = ce que main doit rattraper.
export const GitAheadBehindSchema = z.object({
  base: z.string(),
  head: z.string(),
  ahead: z.number(),
  behind: z.number(),
})
export type GitAheadBehind = z.infer<typeof GitAheadBehindSchema>

// GET /api/projects/{p}/git : vue read-only du SoT bare. `ahead_behind` null si dev/main pas tous deux
// présents ; `logs` = log court par réf protégée existante. Une seule lecture idempotente sert la vue.
export const GitViewSchema = z.object({
  project: z.string(),
  branches: z.array(GitBranchSchema),
  ahead_behind: GitAheadBehindSchema.nullable(),
  logs: z.record(z.string(), z.array(GitLogEntrySchema)),
})
export type GitView = z.infer<typeof GitViewSchema>

// -- Onboarding self-hosted (phase 4c) : check config-requise + credential par entité ---------------

// Racine de confiance du store actif : joignable ? (file = zéro-config ; bws = BWS_ACCESS_TOKEN présent).
// Aucun secret révélé — `detail` est un libellé humain (chemin de clé / api url), jamais une valeur.
export const SecretStoreHealthSchema = z.object({
  backend: z.string(),          // 'file' | 'bws' — pilote la forme du formulaire de liaison
  ready: z.boolean(),
  detail: z.string(),
})
export type SecretStoreHealth = z.infer<typeof SecretStoreHealthSchema>

// Une exigence par projet : un repo à `mirror_remote` a BESOIN d'un token pour pousser le miroir →
// `satisfied` ssi il porte un `credential_ref` (ou n'a pas de miroir). Le front ne recalcule pas (Python).
export const OnboardingRequirementSchema = z.object({
  project: z.string(),
  mirror_remote: z.string().nullable(),
  needs_credential: z.boolean(),
  linked: z.boolean(),
  satisfied: z.boolean(),
})
export type OnboardingRequirement = z.infer<typeof OnboardingRequirementSchema>

// GET /api/onboarding : état de config-requise du 1er démarrage. `complete` = racine prête ET toutes les
// exigences satisfaites (pas de faux-vert). Sert le bandeau non bloquant + le panneau Réglages.
export const OnboardingStatusSchema = z.object({
  secret_store: SecretStoreHealthSchema,
  requirements: z.array(OnboardingRequirementSchema),
  complete: z.boolean(),
  project_count: z.number(),
  first_run: z.boolean(),       // aucun projet encore : instance neuve → le wizard guide (ne dit pas « complet »)
})
export type OnboardingStatus = z.infer<typeof OnboardingStatusSchema>

// POST /api/projects/{p}/credential : lier un credential. `token` = voie fichier (on stocke la valeur),
// `ref` = voie BWS (UUID bring-your-own validé). Exactement l'un des deux ; `label` humain optionnel.
export interface CredentialLinkInput {
  token?: string
  ref?: string
  label?: string
}
