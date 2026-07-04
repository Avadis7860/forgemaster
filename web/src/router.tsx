import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AppShell } from './App'
import { Landing } from './pages/Landing'
import { ProjectWorkspace } from './pages/ProjectWorkspace'
import { RoadmapTab } from './pages/RoadmapTab'
import { DocsTab } from './pages/DocsTab'
import { DispatchTab } from './pages/DispatchTab'
import { GateTab } from './pages/GateTab'
import { GitTab } from './pages/GitTab'
import { FlowTab } from './pages/FlowTab'
import { TerminalTab } from './pages/TerminalTab'
import { SettingsTab } from './pages/SettingsTab'
import { SetupWizard } from './pages/SetupWizard'

// Routing code-based (pas de codegen) — l'échelle du cockpit ne justifie pas le file-based.
const rootRoute = createRootRoute({ component: AppShell })

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: Landing })

// /setup = wizard 1er-démarrage guidé (bienvenue → coffre → 1er projet → miroir+token → prêt).
const setupRoute = createRoute({ getParentRoute: () => rootRoute, path: '/setup', component: SetupWizard })

// /settings = onboarding self-hosted (instance-level, hors projet) : coffre de secrets + token par repo.
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/settings', component: SettingsTab })

// /$project = layout workspace à onglets (IA option A) ; sa route index = l'onglet Roadmap (home projet).
// Dispatch (V3) + Gate (V4) + Terminal (V5) livrés — l'IA option A est complète.
const projectRoute = createRoute({ getParentRoute: () => rootRoute, path: '/$project', component: ProjectWorkspace })
const roadmapRoute = createRoute({ getParentRoute: () => projectRoute, path: '/', component: RoadmapTab })
const docsRoute = createRoute({ getParentRoute: () => projectRoute, path: 'docs', component: DocsTab })
const dispatchRoute = createRoute({ getParentRoute: () => projectRoute, path: 'dispatch', component: DispatchTab })
const gateRoute = createRoute({ getParentRoute: () => projectRoute, path: 'gate', component: GateTab })
const gitRoute = createRoute({ getParentRoute: () => projectRoute, path: 'git', component: GitTab })
const flowRoute = createRoute({ getParentRoute: () => projectRoute, path: 'flow', component: FlowTab })
const terminalRoute = createRoute({ getParentRoute: () => projectRoute, path: 'terminal', component: TerminalTab })

const routeTree = rootRoute.addChildren([
  indexRoute,
  setupRoute,
  settingsRoute,
  projectRoute.addChildren([roadmapRoute, docsRoute, dispatchRoute, gateRoute, gitRoute, flowRoute, terminalRoute]),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
