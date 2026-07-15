// api — client HTTP typé du daemon. Un seul `request` : parse+valide la réponse par un schéma Zod,
// mappe les erreurs domaine du daemon (404 KeyError / 400 ValueError / 422 validation ou fail-closed).
// Rappels de contrat : chemins ASYMÉTRIQUES (/api/projects/{p}/… vs /api/features/{p}/{f}/…).
import type { z } from 'zod'
import {
  BootstrapPreviewSchema,
  BootstrapReportSchema,
  DeploymentActionSchema,
  DeploymentLogsSchema,
  DeploymentsSchema,
  DispatchReportSchema,
  DocsSchema,
  GateStatusSchema,
  GitBlobSchema,
  GitCommitDetailSchema,
  GitDiffSchema,
  GitHistorySchema,
  GitTreeSchema,
  GitViewSchema,
  GitSyncSchema,
  GitReconcileSchema,
  FlowSchema,
  FlowOperationsSchema,
  HealthSchema,
  JobDetailSchema,
  JobsListSchema,
  MergeReportSchema,
  NextSchema,
  OnboardingStatusSchema,
  ProjectSchema,
  ProjectsListSchema,
  RoadmapSchema,
  TypesListSchema,
  type BootstrapRunInput,
  type CredentialLinkInput,
  type CreateProjectInput,
  type MergeInput,
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

// POST lifecycle d'un déploiement (up|down|restart) → déploiement projeté. Factorisé : même forme, même
// schéma de retour. Corps vide (`{}`) : les routes ne prennent pas de body (calque du POST reconcile).
function deploymentAction(project: string, branch: string, action: 'up' | 'down' | 'restart') {
  return request(
    `/api/projects/${encodeURIComponent(project)}/deployments/${encodeURIComponent(branch)}/${action}`,
    DeploymentActionSchema,
    { method: 'POST', body: '{}' },
  )
}

export const api = {
  health: () => request('/health', HealthSchema),

  // Registre des bundles : les types de projet OFFERTS à la création (filtrés par validation, fail-closed).
  // GET idempotent (lecture du filesystem vendoré — goto-safe). Alimente le dropdown de NewProjectForm.
  listTypes: () => request('/api/types', TypesListSchema).then((r) => r.types),

  listProjects: () => request('/api/projects', ProjectsListSchema).then((r) => r.projects),
  getProject: (slug: string) => request(`/api/projects/${encodeURIComponent(slug)}`, ProjectSchema),
  createProject: (input: CreateProjectInput) =>
    request('/api/projects', ProjectSchema, { method: 'POST', body: JSON.stringify(input) }),
  // PATCH partiel : édite le miroir GitHub (rendre GitHub-backed). `null` retire le miroir.
  updateProject: (slug: string, body: { mirror_remote?: string | null }) =>
    request(`/api/projects/${encodeURIComponent(slug)}`, ProjectSchema, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  getRoadmap: (project: string) =>
    request(`/api/projects/${encodeURIComponent(project)}/roadmap`, RoadmapSchema),

  // Vue Git read-only du SoT bare (branches · ahead/behind main↔dev · log par réf). GET idempotent —
  // aucun effet (le runner de boucle visuelle goto-only l'atteint sans risque).
  getGit: (project: string) =>
    request(`/api/projects/${encodeURIComponent(project)}/git`, GitViewSchema),
  // GET /api/projects/{p}/git/sync : écart SoT↔miroir GitHub. RÉSEAU (git fetch), NON-idempotent →
  // JAMAIS via un runner goto-only ni un poll ; déclenché par le refresh manuel de GitPanel uniquement.
  getGitSync: (project: string) =>
    request(`/api/projects/${encodeURIComponent(project)}/git/sync`, GitSyncSchema),
  // POST /api/projects/{p}/git/sync/reconcile : réconciliation **ff-only** (la SEULE mutation git de l'UI, et
  // gatée par construction — jamais de merge non-ff). L'UI montre d'ABORD la décision via le GET `/git/sync`
  // (source unique = l'`state`), puis appelle ce POST pour exécuter — pas de dry-run POST.
  reconcileGitSync: (project: string) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/git/sync/reconcile`,
      GitReconcileSchema,
      { method: 'POST', body: '{}' },
    ),
  // Exploration read-only : arbre d'un dossier + contenu d'un fichier à une réf. GET idempotents.
  getGitTree: (project: string, ref: string, path: string) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/git/tree` +
        `?ref=${encodeURIComponent(ref)}&path=${encodeURIComponent(path)}`,
      GitTreeSchema,
    ),
  getGitBlob: (project: string, ref: string, path: string) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/git/blob` +
        `?ref=${encodeURIComponent(ref)}&path=${encodeURIComponent(path)}`,
      GitBlobSchema,
    ),
  // Intelligence git read-only : détail d'un commit · diff de feature `base...head` · historique fichier.
  getGitCommit: (project: string, sha: string) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/git/commit/${encodeURIComponent(sha)}`,
      GitCommitDetailSchema,
    ),
  getGitDiff: (project: string, base: string, head: string) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/git/diff` +
        `?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`,
      GitDiffSchema,
    ),
  getGitHistory: (project: string, ref: string, path: string) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/git/history` +
        `?ref=${encodeURIComponent(ref)}&path=${encodeURIComponent(path)}`,
      GitHistorySchema,
    ),
  // Flow (flot d'exécution) : opérations découvertes (routes + verbes CLI) + sous-graphe d'appels d'une
  // opération. GET idempotents (index bâti au 1ᵉʳ accès, caché par SHA — aucun effet observable, goto-safe).
  getFlowOperations: (project: string, ref = 'dev') =>
    request(
      `/api/projects/${encodeURIComponent(project)}/flow/operations?ref=${encodeURIComponent(ref)}`,
      FlowOperationsSchema,
    ),
  // `selector` = l'`entry` unique (`file::qualname`) de l'opération. Le backend le résout via `_resolve_entry`
  // (matche `operation` OU `entry`) → on passe l'entry pour lever toute ambiguïté de label. Param d'URL
  // inchangé (`operation`) : le backend accepte l'un ou l'autre.
  getFlow: (project: string, selector: string, ref = 'dev', depth = 6) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/flow` +
        `?operation=${encodeURIComponent(selector)}&ref=${encodeURIComponent(ref)}&depth=${depth}`,
      FlowSchema,
    ),

  // Docs : la carte du projet/outil, lue depuis son repo (SoT). GET idempotent (lecture bare, goto-safe).
  getDocs: (project: string) =>
    request(`/api/projects/${encodeURIComponent(project)}/docs`, DocsSchema),

  // Runtime (P5) : observabilité des déploiements. `getDeployments` = liste pure-DB (GET idempotent,
  // goto-safe) ; `getDeploymentStatus` = reconcile LIVE (`compose ps`, écrit la DB — comme `/git/sync`,
  // rattaché au refresh manuel, jamais au poll goto-only) ; `getDeploymentLogs` = tail borné read-only.
  getDeployments: (project: string) =>
    request(`/api/projects/${encodeURIComponent(project)}/deployments`, DeploymentsSchema),
  getDeploymentStatus: (project: string, branch: string) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/deployments/${encodeURIComponent(branch)}/status`,
      DeploymentActionSchema,
    ),
  getDeploymentLogs: (project: string, branch: string, tail: number) =>
    request(
      `/api/projects/${encodeURIComponent(project)}/deployments/${encodeURIComponent(branch)}` +
        `/logs?tail=${tail}`,
      DeploymentLogsSchema,
    ),
  // Lifecycle mutant (POST) : deploy/stop/restart un déploiement. Jamais atteint par un runner goto-only.
  deploymentUp: (project: string, branch: string) => deploymentAction(project, branch, 'up'),
  deploymentDown: (project: string, branch: string) => deploymentAction(project, branch, 'down'),
  deploymentRestart: (project: string, branch: string) => deploymentAction(project, branch, 'restart'),

  getNext: (project: string, feature: string) =>
    request(
      `/api/features/${encodeURIComponent(project)}/${encodeURIComponent(feature)}/next`,
      NextSchema,
    ),

  // Jobs d'une feature (récents d'abord) — sert la découverte du job en cours à streamer + l'historique.
  listFeatureJobs: (project: string, feature: string) =>
    request(
      `/api/dispatch/${encodeURIComponent(project)}/${encodeURIComponent(feature)}/jobs`,
      JobsListSchema,
    ).then((r) => r.jobs),
  // Détail + transcript AT-REST d'un job (run terminé) — pas de socket ouvert pour un run fini.
  getJob: (jobId: string) => request(`/api/jobs/${encodeURIComponent(jobId)}`, JobDetailSchema),
  // POST dispatch = LONG bloquant (spawn `claude -p`, ≤30 min) : rendu à la fin du run (rapport + job_id).
  dispatch: (project: string, feature: string) =>
    request(
      `/api/dispatch/${encodeURIComponent(project)}/${encodeURIComponent(feature)}`,
      DispatchReportSchema,
      { method: 'POST', body: '{}' },
    ),

  // Vue Gate : statut brut (review/verify) + décision composée en preview GO=false (hold). GET idempotent —
  // aucun effet (le runner de boucle visuelle goto-only peut l'atteindre sans risque).
  getGate: (project: string, feature: string) =>
    request(
      `/api/gate/${encodeURIComponent(project)}/${encodeURIComponent(feature)}`,
      GateStatusSchema,
    ),
  // POST merge SOUS GO HUMAIN — la SEULE mutation. `go:false` ne merge jamais (fail-closed) ; overrides
  // t1/t15 explicites tracés (lèvent un 🔴 reviewer / un Tier-1.5, JAMAIS un Tier-0/natif déterministe).
  merge: (project: string, feature: string, body: MergeInput) =>
    request(
      `/api/merge/${encodeURIComponent(project)}/${encodeURIComponent(feature)}`,
      MergeReportSchema,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  // Onboarding self-hosted : état de config-requise (GET idempotent, aucun secret révélé) + liaison d'un
  // credential (POST : la réponse porte la RÉFÉRENCE, jamais le token) + déliaison (DELETE).
  getOnboarding: () => request('/api/onboarding', OnboardingStatusSchema),
  linkCredential: (project: string, body: CredentialLinkInput) =>
    request(`/api/projects/${encodeURIComponent(project)}/credential`, ProjectSchema, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  unlinkCredential: (project: string) =>
    request(`/api/projects/${encodeURIComponent(project)}/credential`, ProjectSchema, {
      method: 'DELETE',
    }),

  // Amorçage des outils du framework : GET = aperçu idempotent (goto-only, aucun effet/secret) ; POST =
  // adopte les outils du manifeste (idempotent, skip existants). Le token brut ne transite jamais ici.
  getBootstrap: () => request('/api/bootstrap', BootstrapPreviewSchema),
  runBootstrap: (body: BootstrapRunInput = {}) =>
    request('/api/bootstrap', BootstrapReportSchema, { method: 'POST', body: JSON.stringify(body) }),
}
