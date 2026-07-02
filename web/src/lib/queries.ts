// queries — hooks TanStack Query au-dessus du client `api`. Clés centralisées (qk) pour l'invalidation.
// V1 : projets (+ santé). Roadmap/next exposés (stables, consommés dès V2). Dispatch/gate/merge : leurs vagues.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type { CreateProjectInput } from './schemas'

export const qk = {
  health: ['health'] as const,
  projects: ['projects'] as const,
  project: (slug: string) => ['projects', slug] as const,
  roadmap: (project: string) => ['roadmap', project] as const,
  next: (project: string, feature: string) => ['next', project, feature] as const,
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

export function useNext(project: string, feature: string) {
  return useQuery({
    queryKey: qk.next(project, feature),
    queryFn: () => api.getNext(project, feature),
    enabled: Boolean(project && feature),
  })
}
