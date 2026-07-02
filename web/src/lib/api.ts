// api — client HTTP typé du daemon. Un seul `request` : parse+valide la réponse par un schéma Zod,
// mappe les erreurs domaine du daemon (404 KeyError / 400 ValueError / 422 validation ou fail-closed).
// Rappels de contrat : chemins ASYMÉTRIQUES (/api/projects/{p}/… vs /api/features/{p}/{f}/…).
import type { z } from 'zod'
import {
  HealthSchema,
  NextSchema,
  ProjectSchema,
  ProjectsListSchema,
  RoadmapSchema,
  type CreateProjectInput,
} from './schemas'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch (cause) {
    throw new ApiError(0, `daemon injoignable (${String(cause)})`)
  }
  const body = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : res.statusText || `HTTP ${res.status}`
    throw new ApiError(res.status, detail)
  }
  return schema.parse(body)
}

export const api = {
  health: () => request('/health', HealthSchema),

  listProjects: () => request('/api/projects', ProjectsListSchema).then((r) => r.projects),
  getProject: (slug: string) => request(`/api/projects/${encodeURIComponent(slug)}`, ProjectSchema),
  createProject: (input: CreateProjectInput) =>
    request('/api/projects', ProjectSchema, { method: 'POST', body: JSON.stringify(input) }),

  getRoadmap: (project: string) =>
    request(`/api/projects/${encodeURIComponent(project)}/roadmap`, RoadmapSchema),
  getNext: (project: string, feature: string) =>
    request(
      `/api/features/${encodeURIComponent(project)}/${encodeURIComponent(feature)}/next`,
      NextSchema,
    ),
}
