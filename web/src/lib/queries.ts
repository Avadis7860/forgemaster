// queries — hooks TanStack Query au-dessus du client `api`. Clés centralisées (qk) pour l'invalidation.
// V1 : projets (+ santé). Roadmap/next exposés (stables, consommés dès V2). Dispatch/gate/merge : leurs vagues.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type { CreateProjectInput, MergeInput } from './schemas'

export const qk = {
  health: ['health'] as const,
  projects: ['projects'] as const,
  project: (slug: string) => ['projects', slug] as const,
  roadmap: (project: string) => ['roadmap', project] as const,
  git: (project: string) => ['git', project] as const,
  next: (project: string, feature: string) => ['next', project, feature] as const,
  jobs: (project: string, feature: string) => ['jobs', project, feature] as const,
  job: (jobId: string) => ['job', jobId] as const,
  gate: (project: string, feature: string) => ['gate', project, feature] as const,
}

export function useHealth() {
  // Sonde de liveness — rafraîchie périodiquement pour la pastille d'état du daemon.
  return useQuery({ queryKey: qk.health, queryFn: api.health, refetchInterval: 10_000, retry: false })
}

export function useProjects() {
  return useQuery({ queryKey: qk.projects, queryFn: api.listProjects })
}

export function useProject(slug: string) {
  return useQuery({ queryKey: qk.project(slug), queryFn: () => api.getProject(slug), enabled: Boolean(slug) })
}

export function useCreateProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateProjectInput) => api.createProject(input),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.projects }),
  })
}

export function useRoadmap(project: string) {
  return useQuery({
    queryKey: qk.roadmap(project),
    queryFn: () => api.getRoadmap(project),
    enabled: Boolean(project),
  })
}

// Vue Git read-only d'un projet (branches · ahead/behind · log). GET idempotent, pas de poll.
export function useGit(project: string) {
  return useQuery({
    queryKey: qk.git(project),
    queryFn: () => api.getGit(project),
    enabled: Boolean(project),
  })
}

export function useNext(project: string, feature: string) {
  return useQuery({
    queryKey: qk.next(project, feature),
    queryFn: () => api.getNext(project, feature),
    enabled: Boolean(project && feature),
  })
}

// Jobs d'une feature. `pollMs` active un rafraîchissement (utilisé pendant un dispatch pour DÉCOUVRIR le
// nouveau job en cours — le POST bloque et ne rend le job_id qu'à la fin).
export function useFeatureJobs(project: string, feature: string, pollMs: number | false = false) {
  return useQuery({
    queryKey: qk.jobs(project, feature),
    queryFn: () => api.listFeatureJobs(project, feature),
    enabled: Boolean(project && feature),
    refetchInterval: pollMs,
  })
}

// Détail + transcript AT-REST d'un job (run terminé) — lecture HTTP one-shot, sans socket.
export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: qk.job(jobId ?? ''),
    queryFn: () => api.getJob(jobId as string),
    enabled: Boolean(jobId),
  })
}

// Déclenche le dispatch de la NEXT task d'une feature (POST long bloquant). À la résolution, invalide la
// roadmap (la task passe in_progress→done|todo) et la liste des jobs (le run est journalisé).
export function useDispatch(project: string, feature: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.dispatch(project, feature),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.roadmap(project) })
      qc.invalidateQueries({ queryKey: qk.jobs(project, feature) })
    },
  })
}

// Vue Gate d'une feature : statut brut + décision composée (preview GO=false). GET idempotent, pas de poll.
export function useGate(project: string, feature: string) {
  return useQuery({
    queryKey: qk.gate(project, feature),
    queryFn: () => api.getGate(project, feature),
    enabled: Boolean(project && feature),
  })
}

// Merge sous GO humain (la seule mutation). À la résolution, invalide gate (branche potentiellement
// supprimée), roadmap (feature merged, tasks done) et jobs. Le backend reste fail-closed : la mutation
// ne merge que si gate vert ET go — le front n'anticipe jamais la décision (source unique Python).
export function useMerge(project: string, feature: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MergeInput) => api.merge(project, feature, body),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.gate(project, feature) })
      qc.invalidateQueries({ queryKey: qk.roadmap(project) })
      qc.invalidateQueries({ queryKey: qk.jobs(project, feature) })
    },
  })
}
