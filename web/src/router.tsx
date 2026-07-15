import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AppShell } from './App'
import { Landing } from './pages/Landing'
import { ProjectWorkspace } from './pages/ProjectWorkspace'
import { RoadmapTab } from './pages/RoadmapTab'
import { DocsTab } from './pages/DocsTab'
import { TravailTab } from './pages/TravailTab'
import { OpsTab } from './pages/OpsTab'
import { SettingsTab } from './pages/SettingsTab'
import { SetupWizard } from './pages/SetupWizard'

// Routing code-based (pas de codegen) — l'échelle du cockpit ne justifie pas le file-based.
const rootRoute = createRootRoute({ component: AppShell })

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: Landing })

// /setup = wizard 1er-démarrage guidé (bienvenue → coffre → 1er projet → miroir+token → prêt).
const setupRoute = createRoute({ getParentRoute: () => rootRoute, path: '/setup', component: SetupWizard })

// /settings = onboarding self-hosted (instance-level, hors projet) : coffre de secrets + token par repo.
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/settings', component: SettingsTab })

// /$project = layout workspace à onglets ; sa route index = l'onglet Roadmap (home projet). Refonte IA v3 :
// Dispatch + Gate fusionnés en « Travail » (P2) ; Git+Runtime+Flow+Terminal regroupés en « Ops » (P3 : Terminal
// ancre + Runtime fondu + Git drawer + Flow modal, overlays via `?panel=`) ; Accueil (Docs fondu) suit (P4).
const projectRoute = createRoute({ getParentRoute: () => rootRoute, path: '/$project', component: ProjectWorkspace })
const roadmapRoute = createRoute({ getParentRoute: () => projectRoute, path: '/', component: RoadmapTab })
const docsRoute = createRoute({ getParentRoute: () => projectRoute, path: 'docs', component: DocsTab })
const travailRoute = createRoute({ getParentRoute: () => projectRoute, path: 'travail', component: TravailTab })
const opsRoute = createRoute({ getParentRoute: () => projectRoute, path: 'ops', component: OpsTab })

const routeTree = rootRoute.addChildren([
  indexRoute,
  setupRoute,
  settingsRoute,
  projectRoute.addChildren([roadmapRoute, docsRoute, travailRoute, opsRoute]),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
