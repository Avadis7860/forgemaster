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
