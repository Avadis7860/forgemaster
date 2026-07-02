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
