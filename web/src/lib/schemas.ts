// schemas — contrat typé du daemon (SoT côté front). Zod = validation runtime + inférence des types.
// Portée V1→V2 : entités projet/feature/task/roadmap/next. Job/Gate/Merge seront ajoutés dans leurs
// vagues (V3/V4) pour n'avoir aucun schéma non consommé. Miroir exact du contrat exposé par le daemon.
import { z } from 'zod'

export const HealthSchema = z.object({ status: z.string(), version: z.string() })
export type Health = z.infer<typeof HealthSchema>

// Token WS par-instance (garde CSWSH) : le front same-origin le lit puis l'injecte dans le sous-protocole
// des handshakes WS. Cf. daemon.wsguard + lib/ws.tokenProtocols.
export const WsTokenSchema = z.object({ token: z.string() })
export type WsToken = z.infer<typeof WsTokenSchema>

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
  project_type: z.string(),         // bundle semé (v6) — la réponse échoie le type créé (sert la vérif UI)
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
  // `mode` (v12) : `headless` | `interactive`. Une task `interactive` (interview de socle) ne se dispatche PAS
  // en `claude -p` → l'UI propose de la mener en terminal. TOUJOURS émis par le backend (colonne NOT NULL,
  // `list_tasks` SELECT *) → requis, sans default (évite la divergence input/output de Zod).
  mode: z.string(),
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

// Un type de projet OFFERT (registre filesystem filtré par validation) + ses métadonnées de manifeste.
// Source unique du dropdown de création (GET /api/types) — mêmes types que le durcissement CLI.
export const BundleTypeSchema = z.object({
  type: z.string(),               // le nom du type = l'overlay (== project_type pour un bundle valide)
  version: z.string(),
  facets: z.array(z.string()),    // granularités de dispatch adossées (une par dossier .claude/facets/<f>/)
  default_facet: z.string(),
  // déclaration MCP sèche du manifeste (sans secret) : `corpus` = bénéficie du capital-token universel ;
  // `tech_scope` = silo tech dédié au type (pointeur). Optionnel → les types sans déclaration valident.
  mcp: z.object({ corpus: z.boolean().optional(), tech_scope: z.string().optional() }).optional(),
})
export type BundleType = z.infer<typeof BundleTypeSchema>

export const TypesListSchema = z.object({ types: z.array(BundleTypeSchema) })

// Intérieur d'un bundle (explorer P5, GET /api/bundles/{type}/…). L'arbre = les fichiers du bundle composé,
// chacun avec son `group` de curation (méthode/deploy/seed/docs/plomberie) posé par le serveur. Le corps est
// tiré à la demande (un fichier). Read-only, goto-safe (lecture du vendoré, fail-closed sur type/fichier absent).
export const BundleFileEntrySchema = z.object({ path: z.string(), group: z.string() })
export type BundleFileEntry = z.infer<typeof BundleFileEntrySchema>

export const BundleTreeSchema = z.object({
  type: z.string(),
  files: z.array(BundleFileEntrySchema),
})
export type BundleTree = z.infer<typeof BundleTreeSchema>

export const BundleFileSchema = z.object({
  type: z.string(),
  path: z.string(),
  group: z.string(),
  content: z.string(),
})
export type BundleFile = z.infer<typeof BundleFileSchema>

// Capital-token servi par le MCP (explorer d'introspection, GET /api/capital/…). Le daemon est un proxy
// authentifié FIN : il passe le corps MCP tel quel (le serveur mcp-catalogs est la SoT de la forme). Les
// réponses sont HÉTÉROGÈNES par type/layout (tech silo : {path,title,h2s,lead} ; blueprint plat :
// {id,title,tags,status,file}) → schémas TOLÉRANTS (`passthrough`) : on déclare ce qu'on affiche, le reste
// passe sans casser. `wired:false` (porte /status) → l'explorer rend « non câblé » sans tenter de parcours.
export const CapitalStatusSchema = z.object({ wired: z.boolean(), endpoint: z.string() })
export type CapitalStatus = z.infer<typeof CapitalStatusSchema>

export const CapitalTypeSchema = z
  .object({
    id: z.string(),
    layout: z.string().optional(),        // 'silo' | 'flat-collection' — pilote la présence de collections
    query: z.string().optional(),         // 'scoped' | 'canonical' | 'reference'
    unit: z.string().optional(),          // 'chunk' | 'file'
    distilled_of: z.string().nullish(),   // lignée de distillation (blueprint←decision, templates←blueprint)
  })
  .passthrough()
export type CapitalType = z.infer<typeof CapitalTypeSchema>
export const CapitalTypesSchema = z.object({ types: z.array(CapitalTypeSchema) })

export const CapitalCollectionSchema = z
  .object({
    name: z.string(),
    completeness: z.string().optional(),  // 'full' | 'partial' — annotation d'état de la source (silo tech)
    // nullish (pas juste optional) : un silo d'un type `path-derived` (templates, unit=file) n'a NI pages NI
    // chunks → le serveur renvoie `null` explicite (valeur honnête, pas 0). `.optional()` seul rejetterait ce
    // null → « Collections indisponibles ». Même traitement que `last_synced` ci-dessous.
    pages_count: z.number().nullish(),
    chunks_count: z.number().nullish(),
    last_synced: z.string().nullish(),
  })
  .passthrough()
export type CapitalCollection = z.infer<typeof CapitalCollectionSchema>
export const CapitalCollectionsSchema = z.object({
  type: z.string(),
  collections: z.array(CapitalCollectionSchema),
  facets: z.array(z.string()).optional(),
})

export const CapitalSectionSchema = z
  .object({
    id: z.string().optional(),            // corpus plat (blueprint) : la ref de lecture EST l'id
    path: z.string().optional(),          // silo (tech) : la ref de lecture = `<scope>/<path>`
    title: z.string().optional(),
    status: z.string().optional(),        // blueprint : 'active' | 'superseded' (jamais servi si superseded)
    lead: z.string().optional(),          // tech : chapeau de la page
    tags: z.array(z.string()).optional(),
  })
  .passthrough()
export type CapitalSection = z.infer<typeof CapitalSectionSchema>
export const CapitalSectionsSchema = z.object({
  type: z.string(),
  scope: z.string().nullish(),
  sections: z.array(CapitalSectionSchema),
  total: z.number().optional(),
})

export const CapitalBodySchema = z
  .object({
    type: z.string(),
    ref: z.string(),
    title: z.string().optional(),
    body: z.string().optional(),          // blueprint : la prose Markdown
    content: z.string().optional(),       // tech : la prose Markdown
  })
  .passthrough()
export type CapitalBody = z.infer<typeof CapitalBodySchema>

export interface CreateProjectInput {
  slug: string
  name?: string | null
  mirror_remote?: string | null
  kind?: string             // 'project' (défaut) | 'tool'
  project_type?: string     // type de bundle semé (défaut 'generic' côté daemon)
}

// Un template de référence UI OFFERT par la vitrine (GET /api/templates) : le résumé d'un dossier zéro-build
// `web/public/templates/<slug>/` servi statiquement à `/templates/<slug>/…`. `slug` = nom de dossier (URL-safe) ;
// `preview`/`entry` sont des chemins RELATIFS au dossier (le front bâtit `/templates/<slug>/<preview|entry>`).
// Le backend est un scan déterministe fail-closed (dossier mal formé ignoré) → schéma strict, pas de passthrough.
export const TemplateSummarySchema = z.object({
  slug: z.string(),
  name: z.string(),
  tool_type: z.string(),     // archetype (browser-game | front-ts | …) — filtrage « optimal pour tel outil »
  genre: z.string(),         // sous-genre (spatial | …) — tous les browser-games ne sont pas OGame-like
  tags: z.array(z.string()),
  intention: z.string(),     // description courte affichée sous le nom (« pour quel type d'outil »)
  preview: z.string(),       // chemin relatif de la vignette (défaut preview.png)
  entry: z.string(),         // chemin relatif de l'entrée (défaut index.html) — src de l'iframe live
  themes: z.array(z.string()),  // colorimétries swappables embarquées (orbital | reactor | void)
})
export type TemplateSummary = z.infer<typeof TemplateSummarySchema>

export const TemplatesListSchema = z.object({ templates: z.array(TemplateSummarySchema) })

// Résultat de POST /api/projects/{p}/inspire : la feature+task de customisation créée + la graine posée
// (`docs/design/<template>/{brief.md,tokens.css,preview.png}`). Un worker de customisation (à dispatcher)
// relira le brief comme cible visuelle. Champs consommés déclarés (les extras — path/commit — ignorés).
export const InspireResultSchema = z.object({
  project: z.string(),
  template: z.string(),
  feature: z.string(),      // la feature de customisation créée (design-<template>)
  task: z.string(),         // la task de customisation (customize-ui)
  files: z.array(z.string()),   // fichiers de la graine posés (brief.md, tokens.css, preview.png)
})
export type InspireResult = z.infer<typeof InspireResultSchema>

// -- V3 dispatch : job (run worker) + rapport de dispatch + événements de transcript (streamés en WS) --

// Une ligne de `dispatch_jobs` enrichie du `task_slug` (join list_jobs). Champs consommés déclarés ;
// les colonnes non listées sont ignorées par Zod (pas de bruit côté front).
export const JobSchema = z.object({
  id: z.string(),
  task_slug: z.string().nullish(),
  kind: z.string().nullish(),       // 'task' (défaut) | 'review' | 'toolchain' | 'fix' — identité honnête du run
  status: z.string(),
  error: z.string().nullish(),      // snippet d'échec persisté (dispatch_jobs.error) — null si le run n'a pas échoué
  num_turns: z.number().nullish(),
  cost_usd: z.number().nullish(),
  wall_s: z.number().nullish(),
  started_at: z.string().nullish(),
  ended_at: z.string().nullish(),
})
export type Job = z.infer<typeof JobSchema>

export const JobsListSchema = z.object({ jobs: z.array(JobSchema) })

// -- coût token par step→feature→projet (GET /api/projects/{slug}/cost, dispatch/cost.py) --
// Le $ (`cost_usd`) est celui de Claude (`total_cost_usd`), jamais recalculé côté front. Les tokens sont la
// vérité affichée, le $ le repère. Un accumulateur = les 4 types de tokens + leur somme + le $ + le nb de jobs.
const CostAccSchema = z.object({
  cost_usd: z.number(),
  input: z.number(),
  output: z.number(),
  cache_read: z.number(),
  cache_creation: z.number(),
  tokens: z.number(),          // somme des 4 types (la métrique globale)
  n_jobs: z.number(),
})
export type CostAcc = z.infer<typeof CostAccSchema>

const CostStepSchema = CostAccSchema.extend({ task_slug: z.string() })

const CostFeatureSchema = CostAccSchema.extend({
  slug: z.string(),
  steps: z.array(CostStepSchema),   // jobs `task` roulés par task (retries sommés)
  fix: CostAccSchema.nullable(),    // jobs `fix` de la feature (ancre arbitraire → niveau feature, pas step)
})
export type CostFeature = z.infer<typeof CostFeatureSchema>

// Interview de socle (v14) : TOKENS-ONLY. Une session `claude` interactive n'émet pas d'event `result` → pas de
// $ (Claude ne price pas l'interactif). `cost_usd` est donc `null` (jamais un $, distinct d'un $0). Ses tokens
// grossissent `total.tokens` mais PAS `total.cost_usd`.
const CostInterviewSchema = z.object({
  cost_usd: z.null(),
  input: z.number(),
  output: z.number(),
  cache_read: z.number(),
  cache_creation: z.number(),
  tokens: z.number(),
  model: z.string().nullish(),
  n_sessions: z.number(),
})
export type CostInterview = z.infer<typeof CostInterviewSchema>

export const ProjectCostSchema = z.object({
  project: z.string(),
  total: CostAccSchema.extend({ model: z.string().nullish(), n_models: z.number() }),
  features: z.array(CostFeatureSchema),
  nonwork: CostAccSchema,           // overhead review/outillage (compté au total, hors travail)
  interview: CostInterviewSchema.nullable(),   // v14 : coût interview tokens-only ($ = null), ou null si aucune
})
export type ProjectCost = z.infer<typeof ProjectCostSchema>

// Rapport agrégé du POST dispatch (orchestrator.run_feature) : DRAINE la feature (DAG intra-feature) PUIS
// la FINALISE (Tier-0 + review Tier-1) — bloquant, rendu à la FIN. Même forme que le rapport CLI (`cockpit
// run`) : `dispatched`/`ok`/`failed` = COMPTEURS de runs, `finalizations` = review produite par feature.
const FeatureRunSchema = z.object({
  feature: z.string(),
  task: z.string().nullish(),
  ok: z.boolean(),
  reason: z.string(),
})
const FeatureFinalizationSchema = z.object({
  feature: z.string(),
  merge_ready: z.boolean(),
  blockers: z.array(z.string()),
  review: z.record(z.unknown()).nullish(),
})
// Comptes PAR DISPOSITION (report-counts-clarity) : chaque feature dans UN bucket, sans double-compte —
// la source lisible du résumé (« 1 drainée, 1 tenue interview, 2 bloquées ») vs l'agrégat trompeur `dispatched`.
export const RunCountsSchema = z.object({
  drained: z.number(),
  interview: z.number(),
  held_socle: z.number(),
  failed: z.number(),
  blocked: z.number(),
})
export const FeatureRunReportSchema = z.object({
  project: z.string(),
  dispatched: z.number(),
  ok: z.number(),
  failed: z.number(),
  failed_features: z.array(z.string()),
  drained: z.boolean(),
  runs: z.array(FeatureRunSchema),
  needs_interview: z.array(z.string()),
  held_for_socle: z.array(z.string()).default([]), // était OMIS → l'UI perdait ce compte serveur
  finalizations: z.array(FeatureFinalizationSchema),
  merge_ready: z.array(z.string()),
  counts: RunCountsSchema,
  blocked_features: z.array(z.string()).default([]),
  aborted: z.boolean().default(false), // un abort humain a rompu le run (rien mergé, re-runnable)
})
export type FeatureRunReport = z.infer<typeof FeatureRunReportSchema>

// Résultat d'un abort de run (`POST /api/dispatch/{project}/abort`) : combien de workers arrêtés.
export const AbortResultSchema = z.object({
  project: z.string(),
  feature: z.string().nullable(),
  aborted: z.number(),
  jobs: z.array(z.object({ job_id: z.string(), feature: z.string(), pid: z.number().nullable() })),
})
export type AbortResult = z.infer<typeof AbortResultSchema>

// Gate de complétude de la roadmap (`GET /api/projects/{project}/roadmap/check`) — même autorité que le CLI
// `cockpit roadmap check`. Le front n'en lit que `ok` (l'étape « Design rempli » de la frise de lancement).
export const RoadmapCheckSchema = z.object({
  project: z.string(),
  ok: z.boolean(),
  issues: z.array(z.record(z.unknown())),
})
export type RoadmapCheck = z.infer<typeof RoadmapCheckSchema>

// Résultat de « Valider l'interview & clôturer le socle » (`POST /api/dispatch/{project}/reconcile-socle`) :
// compte-rendu HUMAIN de la réconciliation (jamais muet). `status` classe le cas ; le reste RAPPORTE (sha du
// design committé, N tasks socle closes, prochaine étape en clair).
export const ReconcileSocleResultSchema = z.object({
  status: z.enum(['reconciled', 'already_closed', 'interview_incomplete', 'no_socle']),
  completed: z.boolean(),
  feature: z.string().nullable(),
  design_sha: z.string().nullable(),
  socle_tasks_closed: z.number(),
  issues: z.array(z.string()),
  next_step: z.string(),
})
export type ReconcileSocleResult = z.infer<typeof ReconcileSocleResultSchema>

// Résultat d'une passe de correction sur gate rouge (`POST /api/gate/{p}/{f}/refix-dispatch`) : compte-rendu
// HUMAIN (offre, pas autonomie). `status` classe le cas ; `fix_pass/max_passes` situe la borne ; `blockers` +
// `next_step` disent quoi faire ensuite. Le merge n'est JAMAIS déclenché ici (GO humain).
export const RefixResultSchema = z.object({
  status: z.enum(['green', 'still_red', 'exhausted', 'not_refixable', 'not_red', 'dispatch_failed']),
  feature: z.string(),
  gate_green: z.boolean(),
  fix_pass: z.number(),
  max_passes: z.number(),
  blockers: z.array(z.string()),
  head_sha: z.string().nullable(),
  next_step: z.string(),
})
export type RefixResult = z.infer<typeof RefixResultSchema>

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

// Rapport du POST review-dispatch (reviewer.dispatch_reviewer) : (re)produit le verdict Tier-1 SHA-bound.
// `reviewed:false` = readiness-gate/idempotent/best-effort (voir `reason`), pas une erreur.
export const ReviewerDispatchReportSchema = z.object({
  reviewed: z.boolean(),
  reason: z.string(),
  verdict: z.record(z.unknown()).nullish(),
  counts: GateCountsSchema.nullish(),
})
export type ReviewerDispatchReport = z.infer<typeof ReviewerDispatchReportSchema>

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
  refixable: z.boolean(),   // rouge par défaut de code frais → une passe de correction est OFFRABLE
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
  tags: z.array(GitBranchSchema),   // même forme que branches ; le sélecteur de réf les unifie (optgroups)
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

// Dernier commit touchant une entrée d'arbre (façon GitHub : sujet + âge en tête de liste). `.nullish()` sur
// l'entrée = résilient à un daemon antérieur à Phase B.1 (champ absent) ou une entrée sans commit (null).
export const GitEntryCommitSchema = z.object({
  short: z.string(),
  date: z.string(),
  subject: z.string(),
})

// Une entrée d'arbre (dossier du dépôt à une réf) : blob (fichier), tree (dossier) ou commit (sous-module).
// `size` = null pour un arbre. `last_commit` = dernier commit qui la touche (Phase B.1).
export const GitTreeEntrySchema = z.object({
  name: z.string(),
  type: z.enum(['blob', 'tree', 'commit']),
  size: z.number().nullable(),
  sha: z.string(),
  last_commit: GitEntryCommitSchema.nullish(),
})
export type GitTreeEntry = z.infer<typeof GitTreeEntrySchema>

// « latest commit » du dossier courant (barre en tête d'arbre) : auteur · sha · âge · nb total de commits.
export const GitLatestCommitSchema = z.object({
  short: z.string(),
  author: z.string(),
  date: z.string(),
  subject: z.string(),
  count: z.number(),
})

// GET /api/projects/{p}/git/tree?ref=&path= : entrées d'un dossier (dossiers d'abord). Read-only idempotent.
export const GitTreeSchema = z.object({
  project: z.string(),
  ref: z.string(),
  path: z.string(),
  entries: z.array(GitTreeEntrySchema),
  latest_commit: GitLatestCommitSchema.nullish(),
})
export type GitTree = z.infer<typeof GitTreeSchema>

// GET /api/projects/{p}/git/paths?ref= : liste plate récursive des fichiers d'une réf (palette « go to
// file »). `truncated` = la liste dépasse le cap serveur (signalé, jamais silencieux).
export const GitPathsSchema = z.object({
  project: z.string(),
  ref: z.string(),
  paths: z.array(z.string()),
  truncated: z.boolean(),
})
export type GitPaths = z.infer<typeof GitPathsSchema>

// Une correspondance grep : chemin + n° de ligne (1-based) + extrait de la ligne (borné côté serveur).
export const GitSearchMatchSchema = z.object({
  path: z.string(),
  line: z.number(),
  text: z.string(),
})
export type GitSearchMatch = z.infer<typeof GitSearchMatchSchema>

// GET /api/projects/{p}/git/search?ref=&q= : recherche plein-texte (grep) d'une réf (palette « rechercher
// dans le code »). `truncated` = le nb de correspondances dépasse le cap serveur (signalé) ; `count` = total
// avant cap.
export const GitSearchSchema = z.object({
  project: z.string(),
  ref: z.string(),
  q: z.string(),
  results: z.array(GitSearchMatchSchema),
  truncated: z.boolean(),
  count: z.number(),
})
export type GitSearch = z.infer<typeof GitSearchSchema>

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

// Une ligne de blame : le commit auteur qui a introduit la ligne (sha court, auteur, date ISO, résumé).
export const BlameLineSchema = z.object({
  sha: z.string(),
  author: z.string(),
  date: z.string(),
  summary: z.string(),
})
export type BlameLine = z.infer<typeof BlameLineSchema>

// GET /api/projects/{p}/git/blame?ref=&path= : blame ligne-à-ligne (une entrée par ligne du fichier).
export const GitBlameSchema = z.object({
  project: z.string(),
  ref: z.string(),
  path: z.string(),
  lines: z.array(BlameLineSchema),
})
export type GitBlame = z.infer<typeof GitBlameSchema>

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

// -- Frontmap (design-system indexé) : relais du contrat JSON front-map (tokens + primitives + routes) ---
// Chaque verbe : GET /api/projects/{p}/frontmap/{verbe}. GET idempotents (index bâti au 1ᵉʳ accès, caché par
// SHA+version — goto-safe, comme flow). Zod strippe les clés non rendues (props/variants/defaults des
// primitives restent au contrat back mais ne sont pas projetées dans la vue catalogue).
export const FrontmapTokenSchema = z.object({
  name: z.string(),
  value: z.string(),
  group: z.string(),
  source_file: z.string(),
  line: z.number(),
  lead: z.string().nullish(),
})
export type FrontmapToken = z.infer<typeof FrontmapTokenSchema>

export const FrontmapTokensSchema = z.object({
  tokens: z.array(FrontmapTokenSchema),
  count: z.number(),
  engine: z.string().nullish(),
})
export type FrontmapTokens = z.infer<typeof FrontmapTokensSchema>

export const FrontmapPrimitiveSchema = z.object({
  name: z.string(),
  file: z.string(),
  line: z.number(),
  lead: z.string().nullish(),
})
export type FrontmapPrimitive = z.infer<typeof FrontmapPrimitiveSchema>

export const FrontmapPrimitivesSchema = z.object({
  primitives: z.array(FrontmapPrimitiveSchema),
  count: z.number(),
  engine: z.string().nullish(),
})
export type FrontmapPrimitives = z.infer<typeof FrontmapPrimitivesSchema>

export const FrontmapRouteSchema = z.object({
  var: z.string(),
  path: z.string().nullable(),
  full_path: z.string(),
  component: z.string().nullish(),
  parent: z.string().nullable(),
  is_root: z.boolean(),
  file: z.string(),
  line: z.number(),
})
export type FrontmapRoute = z.infer<typeof FrontmapRouteSchema>

export const FrontmapRoutesSchema = z.object({
  routes: z.array(FrontmapRouteSchema),
  count: z.number(),
  engine: z.string().nullish(),
})
export type FrontmapRoutes = z.infer<typeof FrontmapRoutesSchema>

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

// État de câblage du corpus MCP privé (`wired` = ref de secret présente, jamais le secret ; `endpoint`
// effectif ou cible par défaut). Optionnel : une install publique sans corpus privé reste valide.
export const McpStateSchema = z.object({
  wired: z.boolean(),
  endpoint: z.string(),
})
export type McpState = z.infer<typeof McpStateSchema>

// GET /api/onboarding : état de config-requise du 1er démarrage. `complete` = racine prête ET toutes les
// exigences satisfaites (pas de faux-vert). Sert le bandeau non bloquant + le panneau Réglages.
export const OnboardingStatusSchema = z.object({
  secret_store: SecretStoreHealthSchema,
  requirements: z.array(OnboardingRequirementSchema),
  complete: z.boolean(),
  project_count: z.number(),
  first_run: z.boolean(),       // aucun projet encore : instance neuve → le wizard guide (ne dit pas « complet »)
  claude_auth: ClaudeAuthSchema,
  mcp: McpStateSchema,
})
export type OnboardingStatus = z.infer<typeof OnboardingStatusSchema>

// POST /api/onboarding/mcp : câbler le corpus MCP. `secret` = valeur brute (POSSÉDÉE → ref opaque),
// `ref` = UUID BWS (bring-your-own validé) ; exactement l'un. `endpoint` override optionnel.
export interface McpWireInput {
  secret?: string
  ref?: string
  endpoint?: string
}

// Réponse du câblage : la RÉFÉRENCE opaque posée + l'endpoint effectif — jamais le secret.
export const McpWireResultSchema = z.object({
  wired: z.boolean(),
  credential_ref: z.string(),
  endpoint: z.string(),
})
export type McpWireResult = z.infer<typeof McpWireResultSchema>

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

// -- Runtime (P5) : déploiements observables (santé + logs) ------------------------------------------

// Un déploiement projeté à la surface publique (routes/deployments `_public`) : la branche, l'état de run
// (`no_deploy | building | running | stopped | unhealthy`), le port de service, l'URL et le sha du dernier
// deploy. `port`/`url`/`last_deploy_sha` null tant que jamais monté. Le front ne recalcule jamais l'état.
export const DeploymentSchema = z.object({
  branch: z.string(),
  status: z.string(),
  port: z.number().nullable(),
  url: z.string().nullable(),
  last_deploy_sha: z.string().nullable(),
})
export type Deployment = z.infer<typeof DeploymentSchema>

// GET /api/projects/{p}/deployments : les 2 déploiements (main puis dev). Vide honnête = 2 lignes `no_deploy`.
export const DeploymentsSchema = z.object({
  project: z.string(),
  deployments: z.array(DeploymentSchema),
})
export type Deployments = z.infer<typeof DeploymentsSchema>

// GET .../status (reconcile live) et POST up/down/restart renvoient le déploiement projeté.
export const DeploymentActionSchema = z.object({ project: z.string(), deployment: DeploymentSchema })
export type DeploymentAction = z.infer<typeof DeploymentActionSchema>

// GET .../logs?tail=N : les dernières lignes (bornées), vide honnête si jamais monté. Source unique backend.
export const DeploymentLogsSchema = z.object({
  project: z.string(),
  branch: z.string(),
  lines: z.array(z.string()),
})
export type DeploymentLogs = z.infer<typeof DeploymentLogsSchema>
