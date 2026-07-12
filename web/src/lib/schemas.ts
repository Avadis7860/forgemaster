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

// GET /api/projects/{p}/git/sync : écart SoT↔miroir GitHub (RÉSEAU). `state` = rollup projet ; par-branche
// {ahead, behind, state}. Dégradation honnête : `no_mirror` (miroir non câblé) / `unreachable` (fetch KO)
// → `fetched:false`, `branches:{}` (jamais 0/0 faux-vert). `local_ahead`/`remote_ahead` = SoT/miroir en avance.
export const GitSyncStateSchema = z.enum([
  'synced', 'local_ahead', 'remote_ahead', 'diverged', 'no_mirror', 'unreachable',
])
export type GitSyncState = z.infer<typeof GitSyncStateSchema>

export const GitBranchSyncSchema = z.object({
  ahead: z.number(),
  behind: z.number(),
  state: GitSyncStateSchema,
})
export type GitBranchSync = z.infer<typeof GitBranchSyncSchema>

export const GitSyncSchema = z.object({
  project: z.string(),
  remote: z.string(),
  fetched: z.boolean(),
  branches: z.record(z.string(), GitBranchSyncSchema),
  state: GitSyncStateSchema,
})
export type GitSync = z.infer<typeof GitSyncSchema>

// POST /api/projects/{p}/git/sync/reconcile : réconciliation **ff-only** (jamais de merge non-ff). Par
// branche, l'action réellement APPLIQUÉE : `fast_forward` (miroir avancé → ff local), `pushed`/`push_failed`
// (SoT avancé → push miroir), `already_synced`, ou bloquée honnêtement (`blocked_diverged` = vraie divergence,
// `blocked_worktree` = branche sortie dans un worktree, jamais ff). `changed` = au moins une ref a bougé ;
// `blocked` = branches non réconciliables ; `state` = rollup pré-action (l'UI re-lit `/git/sync` pour le badge).
export const GitReconcileActionSchema = z.enum([
  'already_synced', 'fast_forward', 'pushed', 'push_failed', 'blocked_worktree', 'blocked_diverged',
])
export type GitReconcileAction = z.infer<typeof GitReconcileActionSchema>

export const GitBranchReconcileSchema = z.object({
  action: GitReconcileActionSchema,
  from: z.string().optional(),
  to: z.string().optional(),
  reason: z.string().optional(),
})
export type GitBranchReconcile = z.infer<typeof GitBranchReconcileSchema>

export const GitReconcileSchema = z.object({
  project: z.string(),
  remote: z.string(),
  fetched: z.boolean(),
  actions: z.record(z.string(), GitBranchReconcileSchema),
  changed: z.boolean(),
  blocked: z.array(z.string()),
  state: GitSyncStateSchema,
})
export type GitReconcile = z.infer<typeof GitReconcileSchema>

// Une entrée d'arbre (dossier du dépôt à une réf) : blob (fichier), tree (dossier) ou commit (sous-module).
// `size` = null pour un arbre.
export const GitTreeEntrySchema = z.object({
  name: z.string(),
  type: z.enum(['blob', 'tree', 'commit']),
  size: z.number().nullable(),
  sha: z.string(),
})
export type GitTreeEntry = z.infer<typeof GitTreeEntrySchema>

// GET /api/projects/{p}/git/tree?ref=&path= : entrées d'un dossier (dossiers d'abord). Read-only idempotent.
export const GitTreeSchema = z.object({
  project: z.string(),
  ref: z.string(),
  path: z.string(),
  entries: z.array(GitTreeEntrySchema),
})
export type GitTree = z.infer<typeof GitTreeSchema>

// GET /api/projects/{p}/git/blob?ref=&path= : contenu d'un fichier. Gardes L4 : `binary`/`too_large`
// → `content` vide (jamais d'octets bruts) ; `truncated` si le contenu affiché a été coupé.
export const GitBlobSchema = z.object({
  project: z.string(),
  path: z.string(),
  ref: z.string(),
  size: z.number(),
  binary: z.boolean(),
  truncated: z.boolean(),
  too_large: z.boolean(),
  content: z.string(),
})
export type GitBlob = z.infer<typeof GitBlobSchema>

// -- Intelligence git (read-only) : détail d'un commit + diff de feature + historique fichier -------

// Un fichier touché par un commit : `+/-` par fichier (null pour un binaire, drapeau `binary`).
export const GitCommitFileSchema = z.object({
  path: z.string(),
  binary: z.boolean(),
  additions: z.number().nullable(),
  deletions: z.number().nullable(),
})
export type GitCommitFile = z.infer<typeof GitCommitFileSchema>

// GET /api/projects/{p}/git/commit/{sha} : métadonnées d'un commit + fichiers touchés. Read-only.
export const GitCommitDetailSchema = z.object({
  project: z.string(),
  sha: z.string(),
  short: z.string(),
  author: z.string(),
  email: z.string(),
  date: z.string(),
  subject: z.string(),
  body: z.string(),
  files: z.array(GitCommitFileSchema),
})
export type GitCommitDetail = z.infer<typeof GitCommitDetailSchema>

// GET /api/projects/{p}/git/diff?base=&head= : diff unifié `base...head` (three-dot) + fichiers changés.
export const GitDiffSchema = z.object({
  project: z.string(),
  base: z.string(),
  head: z.string(),
  files: z.array(z.string()),
  diff: z.string(),
})
export type GitDiff = z.infer<typeof GitDiffSchema>

// Une entrée d'historique d'un fichier (log -- <path>) : sha + auteur + date ISO + sujet.
export const GitHistoryEntrySchema = z.object({
  sha: z.string(),
  short: z.string(),
  author: z.string(),
  date: z.string(),
  subject: z.string(),
})
export type GitHistoryEntry = z.infer<typeof GitHistoryEntrySchema>

// GET /api/projects/{p}/git/history?ref=&path= : commits touchant un fichier (récents d'abord).
export const GitHistorySchema = z.object({
  project: z.string(),
  ref: z.string(),
  path: z.string(),
  commits: z.array(GitHistoryEntrySchema),
})
export type GitHistory = z.infer<typeof GitHistorySchema>

// -- Flow (flot d'exécution) : opérations découvertes + sous-graphe d'appels d'une opération ---------

// Une opération = un entry point découvert (route API ou verbe CLI). Alimente le sélecteur de l'onglet Flow.
export const FlowOperationSchema = z.object({
  operation: z.string(),      // "GET /api/…" (route) ou "cli:<verbe>"
  entry: z.string(),          // "file::qualname" du caller racine
  kind: z.string(),           // 'route' | 'cli'
})
export type FlowOperation = z.infer<typeof FlowOperationSchema>

// GET /api/projects/{p}/flow/operations : les opérations + le moteur d'extraction (calls-py-v1).
export const FlowOperationsSchema = z.object({
  operations: z.array(FlowOperationSchema),
  engine: z.string().nullable(),
})
export type FlowOperations = z.infer<typeof FlowOperationsSchema>

// Un nœud du flot : une fonction/méthode traversée (`id` = file::qualname). `label`/`file` en dérivent.
export const FlowNodeSchema = z.object({ id: z.string(), label: z.string(), file: z.string() })
export type FlowNode = z.infer<typeof FlowNodeSchema>

// Une arête = un site d'appel. `to`=null ⇒ INDIRECT (callee non résolu statiquement) : `via` nomme le
// mécanisme suspecté (canal d'honnêteté). `order` = position de l'appel dans le corps ; `branch` = gardes.
export const FlowEdgeSchema = z.object({
  from: z.string(),
  to: z.string().nullable(),
  callee_name: z.string(),
  resolution: z.string(),     // direct | import | self | heuristic | indirect
  kind: z.string(),           // call | ctor | indirect
  via: z.string().nullable(),
  order: z.number(),
  branch: z.array(z.string()),
})
export type FlowEdge = z.infer<typeof FlowEdgeSchema>

// Stats du sous-graphe — `indirect_ratio` = fraction d'appels non résolus (bandeau d'honnêteté visuelle).
export const FlowStatsSchema = z.object({
  nodes: z.number(),
  edges: z.number(),
  edges_indirect: z.number(),
  indirect_ratio: z.number(),
  max_depth: z.number(),
})
export type FlowStats = z.infer<typeof FlowStatsSchema>

// GET /api/projects/{p}/flow?operation= : le sous-graphe d'une opération. `ok:false` (introuvable/ambiguë)
// → `reason` porte l'explication, nodes/edges vides. Le front ne recalcule jamais le graphe (source Python).
export const FlowSchema = z.object({
  ok: z.boolean(),
  operation: z.string().nullish(),
  entry: z.string().nullish(),
  reason: z.string().nullish(),
  nodes: z.array(FlowNodeSchema).optional(),   // absents sur ok:false → coalescés côté rendu
  edges: z.array(FlowEdgeSchema).optional(),
  stats: FlowStatsSchema.nullish(),
})
export type Flow = z.infer<typeof FlowSchema>

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

// Auth Claude de l'HÔTE : la machine peut-elle spawner des workers `claude` ? `source` ∈
// {credentials-file, env-api-key, env-oauth, null} — la PRÉSENCE, jamais la valeur du token. Axe
// **orthogonal** à `complete` (config du store) : un cockpit « complet » côté secrets refuse quand même de
// dispatcher tant que cette machine n'a pas fait `claude login`. Surfacé pour que l'usage ne soit jamais silencieux.
export const ClaudeAuthSchema = z.object({
  authenticated: z.boolean(),
  source: z.string().nullable(),
})
export type ClaudeAuth = z.infer<typeof ClaudeAuthSchema>

// GET /api/onboarding : état de config-requise du 1er démarrage. `complete` = racine prête ET toutes les
// exigences satisfaites (pas de faux-vert). Sert le bandeau non bloquant + le panneau Réglages.
export const OnboardingStatusSchema = z.object({
  secret_store: SecretStoreHealthSchema,
  requirements: z.array(OnboardingRequirementSchema),
  complete: z.boolean(),
  project_count: z.number(),
  first_run: z.boolean(),       // aucun projet encore : instance neuve → le wizard guide (ne dit pas « complet »)
  claude_auth: ClaudeAuthSchema,
})
export type OnboardingStatus = z.infer<typeof OnboardingStatusSchema>

// POST /api/projects/{p}/credential : lier un credential. `token` = voie fichier (on stocke la valeur),
// `ref` = voie BWS (UUID bring-your-own validé). Exactement l'un des deux ; `label` humain optionnel.
export interface CredentialLinkInput {
  token?: string
  ref?: string
  label?: string
}

// -- Amorçage « outils du framework » (bootstrap) : manifeste sous COCKPIT_HOME → adoption idempotente ----

// Un outil du manifeste + son état d'adoption (`adopted` = slug déjà présent). Aucun secret exposé.
export const BootstrapToolSchema = z.object({
  slug: z.string(),
  source_url: z.string(),
  kind: z.string(),
  adopted: z.boolean(),
})
export type BootstrapTool = z.infer<typeof BootstrapToolSchema>

// GET /api/bootstrap : aperçu idempotent. `available` = manifeste présent & valide ; `adopted`/`total`
// = combien des outils sont déjà rangés. Manifeste absent → available:false (install générique).
export const BootstrapPreviewSchema = z.object({
  available: z.boolean(),
  tools: z.array(BootstrapToolSchema),
  adopted: z.number(),
  total: z.number(),
})
export type BootstrapPreview = z.infer<typeof BootstrapPreviewSchema>

// POST /api/bootstrap : rapport d'amorçage (idempotent). `created`/`skipped` = slugs ; `failed` = erreurs
// isolées par entrée (la boucle continue). `available:false` = manifeste absent (no-op propre).
export const BootstrapReportSchema = z.object({
  created: z.array(z.string()),
  skipped: z.array(z.string()),
  failed: z.array(z.object({ slug: z.string(), error: z.string() })),
  available: z.boolean(),
})
export type BootstrapReport = z.infer<typeof BootstrapReportSchema>

// Corps du POST : réf credential DÉJÀ stockée (repos privés) ; absente = adoption anonyme (publics).
export interface BootstrapRunInput {
  shared_ref?: string | null
}

// GET /api/projects/{p}/docs : la carte docs d'un projet, LUE depuis son repo (SoT). `found:false` = ni carte
// `docs/tool-card.md` ni `README.md` → l'UI affiche un EmptyState. `content` = Markdown brut (rendu client).
export const DocsSchema = z.object({
  project: z.string(),
  found: z.boolean(),
  ref: z.string().nullable(),
  path: z.string().nullable(),
  content: z.string(),
  truncated: z.boolean(),
})
export type Docs = z.infer<typeof DocsSchema>
