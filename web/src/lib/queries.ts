// queries — hooks TanStack Query au-dessus du client `api`. Clés centralisées (qk) pour l'invalidation.
// V1 : projets (+ santé). Roadmap/next exposés (stables, consommés dès V2). Dispatch/gate/merge : leurs vagues.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type { BootstrapRunInput, CredentialLinkInput, CreateProjectInput, McpWireInput, MergeInput } from './schemas'

export const qk = {
  health: ['health'] as const,
  wsToken: ['ws-token'] as const,
  types: ['types'] as const,
  templates: ['templates'] as const,
  bundleTree: (type: string) => ['bundle-tree', type] as const,
  bundleFile: (type: string, path: string) => ['bundle-file', type, path] as const,
  capitalStatus: ['capital-status'] as const,
  capitalTypes: ['capital-types'] as const,
  capitalCollections: (type: string) => ['capital-collections', type] as const,
  capitalSections: (type: string, scope: string | null) =>
    ['capital-sections', type, scope ?? ''] as const,
  capitalBody: (type: string, ref: string) => ['capital-body', type, ref] as const,
  projects: ['projects'] as const,
  project: (slug: string) => ['projects', slug] as const,
  cost: (project: string) => ['cost', project] as const,
  reliability: (project: string) => ['reliability', project] as const,
  roadmap: (project: string) => ['roadmap', project] as const,
  roadmapCheck: (project: string) => ['roadmap-check', project] as const,
  git: (project: string) => ['git', project] as const,
  gitSync: (project: string) => ['git-sync', project] as const,
  gitTree: (project: string, ref: string, path: string) => ['git-tree', project, ref, path] as const,
  gitBlob: (project: string, ref: string, path: string) => ['git-blob', project, ref, path] as const,
  gitCommit: (project: string, sha: string) => ['git-commit', project, sha] as const,
  gitDiff: (project: string, base: string, head: string) => ['git-diff', project, base, head] as const,
  gitHistory: (project: string, ref: string, path: string) =>
    ['git-history', project, ref, path] as const,
  gitPaths: (project: string, ref: string) => ['git-paths', project, ref] as const,
  gitBlame: (project: string, ref: string, path: string) =>
    ['git-blame', project, ref, path] as const,
  gitSearch: (project: string, ref: string, q: string) =>
    ['git-search', project, ref, q] as const,
  docs: (project: string) => ['docs', project] as const,
  deployments: (project: string) => ['deployments', project] as const,
  deploymentLogs: (project: string, branch: string, tail: number) =>
    ['deployment-logs', project, branch, tail] as const,
  flowOps: (project: string, ref: string) => ['flow-ops', project, ref] as const,
  flow: (project: string, selector: string, ref: string, depth: number) =>
    ['flow', project, selector, ref, depth] as const,
  frontmap: (project: string, view: string, ref: string) => ['frontmap', project, view, ref] as const,
  next: (project: string, feature: string) => ['next', project, feature] as const,
  jobs: (project: string, feature: string) => ['jobs', project, feature] as const,
  job: (jobId: string) => ['job', jobId] as const,
  gate: (project: string, feature: string) => ['gate', project, feature] as const,
  onboarding: ['onboarding'] as const,
  bootstrap: ['bootstrap'] as const,
  alerts: ['alerts'] as const,
}

export function useHealth() {
  // Sonde de liveness — rafraîchie périodiquement pour la pastille d'état du daemon.
  return useQuery({ queryKey: qk.health, queryFn: api.health, refetchInterval: 10_000, retry: false })
}

// Token WS par-instance (garde CSWSH) : chargé une fois, immuable dans la session (staleTime infini, pas de
// poll). Les consommateurs WS (terminal, transcript de dispatch) l'appellent en interne et n'ouvrent le
// WebSocket qu'une fois le token présent (sinon le handshake serait refusé 1008). Retourne `string | undefined`.
export function useWsToken(): string | undefined {
  return useQuery({ queryKey: qk.wsToken, queryFn: api.wsToken, staleTime: Infinity }).data?.token
}

// Les types de projet offerts (registre filtré par validation). Stable (change au dépôt d'un overlay) →
// staleTime élevé. Alimente le dropdown de création ; GET idempotent, pas de poll.
export function useTypes() {
  return useQuery({ queryKey: qk.types, queryFn: api.listTypes, staleTime: 60_000 })
}

export function useProjects() {
  return useQuery({ queryKey: qk.projects, queryFn: api.listProjects })
}

// Vitrine des templates de référence UI. Stable (change au dépôt d'un template) → staleTime élevé, pas de
// poll. GET idempotent, goto-safe ; vide honnête si aucun template servi.
export function useTemplates() {
  return useQuery({ queryKey: qk.templates, queryFn: api.listTemplates, staleTime: 60_000 })
}

// Intérieur d'un bundle (explorer P5). Stable (change au dépôt d'un overlay) → staleTime élevé, pas de poll.
// `enabled` garde la requête tant qu'aucun type/fichier n'est choisi (GET idempotents, goto-safe).
export function useBundleTree(type: string) {
  return useQuery({
    queryKey: qk.bundleTree(type),
    queryFn: () => api.getBundleTree(type),
    enabled: !!type,
    staleTime: 60_000,
  })
}

export function useBundleFile(type: string, path: string) {
  return useQuery({
    queryKey: qk.bundleFile(type, path),
    queryFn: () => api.getBundleFile(type, path),
    enabled: !!type && !!path,
    staleTime: 60_000,
  })
}

// Capital-token servi par le MCP (explorer d'introspection). `/status` (porte) : léger, sans réseau côté
// daemon → poll doux pour refléter un câblage à chaud (wizard) sans recharger. Les parcours sont `enabled`
// en cascade (types → collections → sections → corps) et ne partent QUE si le MCP est câblé (`enabledWired`),
// pour ne pas marteler un 503 quand `wired:false`. `retry:false` : un 503 honnête ne se re-tente pas.
export function useCapitalStatus() {
  return useQuery({
    queryKey: qk.capitalStatus,
    queryFn: api.getCapitalStatus,
    refetchInterval: 15_000,
    retry: false,
  })
}

export function useCapitalTypes(enabledWired: boolean) {
  return useQuery({
    queryKey: qk.capitalTypes,
    queryFn: api.getCapitalTypes,
    enabled: enabledWired,
    staleTime: 60_000,
    retry: false,
  })
}

export function useCapitalCollections(type: string, enabledWired: boolean) {
  return useQuery({
    queryKey: qk.capitalCollections(type),
    queryFn: () => api.getCapitalCollections(type),
    enabled: enabledWired && !!type,
    staleTime: 60_000,
    retry: false,
  })
}

export function useCapitalSections(type: string, scope: string | null, enabled: boolean) {
  return useQuery({
    queryKey: qk.capitalSections(type, scope),
    queryFn: () => api.getCapitalSections(type, scope),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

export function useCapitalBody(type: string, ref: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.capitalBody(type, ref),
    queryFn: () => api.getCapitalBody(type, ref),
    enabled: enabled && !!ref,
    staleTime: 60_000,
    retry: false,
  })
}

export function useProject(slug: string) {
  return useQuery({ queryKey: qk.project(slug), queryFn: () => api.getProject(slug), enabled: Boolean(slug) })
}

// Coût token agrégé du projet (total + par feature/step + overhead review). GET idempotent, pas de poll —
// le refresh manuel (RefreshButton) réconcilie après un drain. Le $ vient de Claude, pas recalculé.
export function useProjectCost(project: string) {
  return useQuery({
    queryKey: qk.cost(project), queryFn: () => api.projectCost(project), enabled: Boolean(project),
  })
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

// Gate de complétude de la roadmap (`ok`) — alimente l'étape « Design rempli » de la frise de lancement.
export function useRoadmapCheck(project: string) {
  return useQuery({
    queryKey: qk.roadmapCheck(project),
    queryFn: () => api.getRoadmapCheck(project),
    enabled: Boolean(project),
  })
}

// « Valider l'interview & clôturer le socle » (action nommée par son résultat). À la résolution, invalide la
// roadmap (socle → done) ET son check → la frise de lancement avance toute seule (jamais un état deviné).
export function useReconcileSocle(project: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.reconcileSocle(project),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.roadmap(project) })
      qc.invalidateQueries({ queryKey: qk.roadmapCheck(project) })
    },
  })
}

// « Inspirer un projet de ce template » : applique un template UI de référence (crée la feature+task de
// customisation `design-<template>` + sème la graine). À la résolution, invalide la roadmap du projet
// (nouvelle feature) et la liste projets → l'UI reflète le travail créé, jamais un état deviné.
export function useInspireProject(project: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (template: string) => api.inspire(project, template),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.roadmap(project) })
      qc.invalidateQueries({ queryKey: qk.projects })
    },
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

// Sync SoT↔miroir GitHub : RÉSEAU (git fetch), NON-idempotent → `enabled:false`, JAMAIS auto ni greffé sur
// `useGit` no-poll. Déclenché À LA MAIN par le RefreshButton de GitExplorer (`.refetch()`). `staleTime`/`gcTime`
// Infinity : le résultat manuel PERSISTE en cache — le rail (EntityCard) le relit en lecture seule (même
// queryKey, `enabled:false`) pour un dot rollup, sans jamais déclencher de fetch réseau.
export function useGitSync(project: string) {
  return useQuery({
    queryKey: qk.gitSync(project),
    queryFn: () => api.getGitSync(project),
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })
}

// Réconciliation SoT↔miroir **ff-only** (POST) — la SEULE mutation git de l'UI. Preview d'ABORD via le GET
// `useGitSync` (source unique = l'`state` par branche), puis ce POST exécute. À la résolution : invalide la vue
// git read-only (branches/log/ahead-behind ont pu bouger) + les projets (dot rail) ; le badge sync lui-même est
// re-fetché à la main par l'appelant (`useGitSync` est enabled:false — un invalidate ne le rejouerait pas).
export function useReconcileSync(project: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.reconcileGitSync(project),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.git(project) })
      qc.invalidateQueries({ queryKey: qk.projects })
    },
  })
}

// Carte docs d'un projet (lue depuis son repo). GET idempotent ; stable (la doc change peu).
export function useDocs(project: string) {
  return useQuery({
    queryKey: qk.docs(project),
    queryFn: () => api.getDocs(project),
    enabled: Boolean(project),
    staleTime: 60_000,
  })
}

// -- Runtime (P5) : déploiements observables (santé + logs + lifecycle) ------------------------------

// Les 2 déploiements (main/dev) d'un projet — liste PURE-DB (GET idempotent). Source des cartes Runtime.
export function useDeployments(project: string) {
  return useQuery({
    queryKey: qk.deployments(project),
    queryFn: () => api.getDeployments(project),
    enabled: Boolean(project),
  })
}

// Reconcile LIVE des 2 déploiements (`compose ps` par branche → écrit la DB). Modelé en mutation : c'est un
// GET mais à effet (rapproche la DB du réel) → déclenché À LA MAIN par le RefreshButton de RuntimeStrip, jamais
// en poll (calque `/git/sync`). À la résolution, invalide la liste → les cartes relisent l'état réconcilié.
export function useReconcileDeployments(project: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () =>
      Promise.all([api.getDeploymentStatus(project, 'main'), api.getDeploymentStatus(project, 'dev')]),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.deployments(project) }),
  })
}

// Tail borné des logs d'un déploiement. `enabled` piloté par l'ouverture du log viewer (jamais auto) ;
// `staleTime:0` → un refetch relit toujours du frais. Le tail fait partie de la clé (changer = nouvelle vue).
export function useDeploymentLogs(project: string, branch: string, tail: number, enabled: boolean) {
  return useQuery({
    queryKey: qk.deploymentLogs(project, branch, tail),
    queryFn: () => api.getDeploymentLogs(project, branch, tail),
    enabled: Boolean(project && branch && enabled),
    staleTime: 0,
    retry: false,
  })
}

// Lifecycle mutant (deploy/stop/restart) d'un déploiement. Chaque mutation invalide la liste (l'état de run
// change) — le front ne devine jamais le nouvel état, il relit la source Python.
function useDeploymentAction(project: string, branch: string, fn: (p: string, b: string) => Promise<unknown>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => fn(project, branch),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.deployments(project) }),
  })
}
export const useDeploy = (project: string, branch: string) =>
  useDeploymentAction(project, branch, api.deploymentUp)
export const useStopDeployment = (project: string, branch: string) =>
  useDeploymentAction(project, branch, api.deploymentDown)
export const useRestartDeployment = (project: string, branch: string) =>
  useDeploymentAction(project, branch, api.deploymentRestart)

// Arbre d'un dossier du dépôt à une réf (explorateur). GET idempotent, pas de poll.
export function useGitTree(project: string, ref: string, path: string) {
  return useQuery({
    queryKey: qk.gitTree(project, ref, path),
    queryFn: () => api.getGitTree(project, ref, path),
    enabled: Boolean(project && ref),
  })
}

// Contenu d'un fichier à une réf (explorateur). Activé seulement quand un fichier est sélectionné.
export function useGitBlob(project: string, ref: string, path: string) {
  return useQuery({
    queryKey: qk.gitBlob(project, ref, path),
    queryFn: () => api.getGitBlob(project, ref, path),
    enabled: Boolean(project && ref && path),
  })
}

// Détail d'un commit (métadonnées + fichiers touchés). Activé quand un commit est sélectionné.
export function useGitCommit(project: string, sha: string) {
  return useQuery({
    queryKey: qk.gitCommit(project, sha),
    queryFn: () => api.getGitCommit(project, sha),
    enabled: Boolean(project && sha),
  })
}

// Diff de feature `base...head` (three-dot). Activé quand base et head sont posés.
export function useGitDiff(project: string, base: string, head: string) {
  return useQuery({
    queryKey: qk.gitDiff(project, base, head),
    queryFn: () => api.getGitDiff(project, base, head),
    enabled: Boolean(project && base && head),
  })
}

// Historique des commits touchant un fichier à une réf. Activé quand un fichier est sélectionné.
export function useGitHistory(project: string, ref: string, path: string) {
  return useQuery({
    queryKey: qk.gitHistory(project, ref, path),
    queryFn: () => api.getGitHistory(project, ref, path),
    enabled: Boolean(project && ref && path),
  })
}

// Liste plate des fichiers d'une réf (palette « go to file »). `enabled` = paresseux : on ne tire la liste
// que lorsque la palette est ouverte (pas au montage de l'explorateur). SHA-bound (change au commit).
export function useGitPaths(project: string, ref: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.gitPaths(project, ref),
    queryFn: () => api.getGitPaths(project, ref),
    enabled: Boolean(project && ref && enabled),
  })
}

// Blame ligne-à-ligne d'un fichier. `enabled` = paresseux : tiré seulement quand le toggle Blame est actif.
export function useGitBlame(project: string, ref: string, path: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.gitBlame(project, ref, path),
    queryFn: () => api.getGitBlame(project, ref, path),
    enabled: Boolean(project && ref && path && enabled),
  })
}

// Recherche plein-texte (grep) d'une réf. `enabled` = paresseux : tiré seulement quand la palette est ouverte
// ET la requête non vide. Clé incluant `q` → chaque requête est cachée séparément (SHA+q-bound).
export function useGitSearch(project: string, ref: string, q: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.gitSearch(project, ref, q),
    queryFn: () => api.getGitSearch(project, ref, q),
    enabled: Boolean(project && ref && q && enabled),
  })
}

// Flow : opérations découvertes d'un projet (sélecteur). GET idempotent, pas de poll. `staleTime` élevé —
// l'index est SHA-bound, il ne change qu'à un nouveau commit sur `ref`.
export function useFlowOperations(project: string, ref = 'dev') {
  return useQuery({
    queryKey: qk.flowOps(project, ref),
    queryFn: () => api.getFlowOperations(project, ref),
    enabled: Boolean(project),
    staleTime: 60_000,
  })
}

// Sous-graphe de flot d'une opération. `selector` = l'`entry` unique (`file::qualname`) de l'opération, PAS
// son label : deux opérations peuvent partager un label (ex. `cli:main` dans deux fichiers) et l'adresser par
// label en masquerait une. Activé seulement quand une opération est sélectionnée. GET idempotent.
export function useFlow(project: string, selector: string, ref = 'dev', depth = 6) {
  return useQuery({
    queryKey: qk.flow(project, selector, ref, depth),
    queryFn: () => api.getFlow(project, selector, ref, depth),
    enabled: Boolean(project && selector),
    staleTime: 60_000,
  })
}

// Frontmap : design-system indexé d'un projet (tokens/primitives/routes). GET idempotents, pas de poll ;
// `staleTime` élevé — l'index est (SHA,version)-bound, il ne change qu'à un nouveau commit sur `ref`.
export function useFrontmapTokens(project: string, ref = 'dev') {
  return useQuery({
    queryKey: qk.frontmap(project, 'tokens', ref),
    queryFn: () => api.getFrontmapTokens(project, ref),
    enabled: Boolean(project),
    staleTime: 60_000,
  })
}

export function useFrontmapPrimitives(project: string, ref = 'dev') {
  return useQuery({
    queryKey: qk.frontmap(project, 'primitives', ref),
    queryFn: () => api.getFrontmapPrimitives(project, ref),
    enabled: Boolean(project),
    staleTime: 60_000,
  })
}

export function useFrontmapRoutes(project: string, ref = 'dev') {
  return useQuery({
    queryKey: qk.frontmap(project, 'routes', ref),
    queryFn: () => api.getFrontmapRoutes(project, ref),
    enabled: Boolean(project),
    staleTime: 60_000,
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

// Déclenche le dispatch d'une feature (POST long bloquant : draine le DAG PUIS finalise → review produite).
// À la résolution, invalide la roadmap (tasks done|todo), les jobs (runs journalisés) ET le gate (le verdict
// Tier-1 vient d'être produit → `review.present`, GO potentiellement activable sans re-clic).
export function useDispatch(project: string, feature: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.dispatch(project, feature),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.roadmap(project) })
      qc.invalidateQueries({ queryKey: qk.jobs(project, feature) })
      qc.invalidateQueries({ queryKey: qk.gate(project, feature) })
    },
  })
}

// Arrête le run en cours de la feature (bouton « Arrêter le run ») : tue les workers, libère le mutex.
// À la résolution, invalide les jobs (le job passe `killed`) ET la roadmap (la task revient `todo`) → le
// front relit la vérité serveur, jamais un état deviné (l'abort backend est l'autorité).
export function useAbort(project: string, feature: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.abortRun(project, feature),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.jobs(project, feature) })
      qc.invalidateQueries({ queryKey: qk.roadmap(project) })
    },
  })
}

// Re-lance la review Tier-1 (filet anti-dead-end quand elle est absente/périmée). À la résolution, invalide
// le gate (le verdict ré-ancré est relu → `review.present`/`fresh`).
export function useReviewDispatch(project: string, feature: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.reviewDispatch(project, feature),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.gate(project, feature) })
    },
  })
}

// Dispatche UNE passe de correction sur un gate rouge (offre bornée). À la résolution, invalide le gate (ré-
// évalué sur le nouveau HEAD) ET les jobs (le job `kind='fix'` apparaît dans la liste, suivable en live).
export function useRefixDispatch(project: string, feature: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.refixDispatch(project, feature),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.gate(project, feature) })
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

// -- Onboarding self-hosted : état de config-requise + liaison/déliaison d'un credential -------------

// État d'onboarding (backend du store + exigences par projet + complete). Rafraîchi périodiquement : le
// bandeau non bloquant doit refléter un token lié en CLI ou depuis un autre onglet sans reload.
export function useOnboarding() {
  return useQuery({ queryKey: qk.onboarding, queryFn: api.getOnboarding, refetchInterval: 15_000 })
}

// Après une (dé)liaison, invalider onboarding (l'exigence bascule), les projets (rail) et le projet ciblé
// (sa `credential_ref` change) — le front ne devine jamais l'état, il relit la source Python.
function invalidateCredential(qc: ReturnType<typeof useQueryClient>, project: string) {
  qc.invalidateQueries({ queryKey: qk.onboarding })
  qc.invalidateQueries({ queryKey: qk.projects })
  qc.invalidateQueries({ queryKey: qk.project(project) })
}

export function useLinkCredential(project: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CredentialLinkInput) => api.linkCredential(project, body),
    onSuccess: () => invalidateCredential(qc, project),
  })
}

export function useUnlinkCredential(project: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.unlinkCredential(project),
    onSuccess: () => invalidateCredential(qc, project),
  })
}

// Câble le corpus MCP privé (instance-level, hors projet). À la résolution, invalide onboarding : l'étape
// « corpus MCP » du wizard bascule sur `wired` (le front relit la source Python, ne devine pas).
export function useWireMcp() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: McpWireInput) => api.wireMcp(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.onboarding }),
  })
}

// Configure/retire le miroir GitHub d'un projet (le rend GitHub-backed → un token devient requis). Invalide
// onboarding + projets + projet (même empreinte qu'une (dé)liaison : l'exigence bascule).
export function useSetMirror(project: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (mirror: string | null) => api.updateProject(project, { mirror_remote: mirror }),
    onSuccess: () => invalidateCredential(qc, project),
  })
}

// -- Amorçage « outils du framework » : aperçu idempotent (GET) + exécution (POST) -------------------

// Aperçu du manifeste d'outils (disponible ? combien déjà rangés ?). GET idempotent — sert l'étape wizard
// et reste atteignable par le runner goto-only (aucun effet, aucun secret).
export function useBootstrap() {
  return useQuery({ queryKey: qk.bootstrap, queryFn: api.getBootstrap })
}

// Déclenche l'adoption des outils du manifeste (idempotent). À la résolution, invalide bootstrap (l'aperçu
// bascule adopted), les projets (le rail gagne la section Outils) et onboarding (nouvelles entités).
export function useRunBootstrap() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BootstrapRunInput = {}) => api.runBootstrap(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.bootstrap })
      qc.invalidateQueries({ queryKey: qk.projects })
      qc.invalidateQueries({ queryKey: qk.onboarding })
    },
  })
}

// Alertes (v17, no-silent-block) : le centre d'alertes du header. Poll court (~5 s) — toujours monté, il
// pousse tout blocage de drain sans que l'humain navigue vers la page de la feature bloquée. `staleTime` 0 :
// le badge doit dire vrai. Retourne { alerts, count } (vide honnête si le daemon est injoignable → error géré).
export function useAlerts() {
  return useQuery({ queryKey: qk.alerts, queryFn: api.listAlerts, refetchInterval: 5_000, retry: 1 })
}

// Acquitter une alerte (open→acked) : elle sort du compteur. Invalide la liste au settle pour rafraîchir
// le badge immédiatement (sans attendre le prochain poll).
export function useAckAlert() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.ackAlert(id),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.alerts }),
  })
}

// Fiabilité du gate vert (v18) : taux + merges verts d'un projet. GET idempotent, pas de poll — refresh manuel
// (réconcilie après un drain / une marque). La donnée sert la décision d'auto-merge (E3) + le jugement humain.
export function useProjectReliability(project: string) {
  return useQuery({
    queryKey: qk.reliability(project), queryFn: () => api.projectReliability(project),
    enabled: Boolean(project),
  })
}

// Marquer l'issue aval d'un merge vert (revert/refix constaté après coup). Invalide au settle pour rafraîchir
// le taux immédiatement — l'attribution est humaine, il n'y a pas de signal automatique.
export function useMarkOutcome(project: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: { feature: string; outcome: string; note?: string; sha?: string }) =>
      api.markOutcome(project, input),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.reliability(project) }),
  })
}
