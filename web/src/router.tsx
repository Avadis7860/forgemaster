import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AppShell } from './App'
import { Landing } from './pages/Landing'
import { ProjectWorkspace } from './pages/ProjectWorkspace'
import { RoadmapTab } from './pages/RoadmapTab'
import { DispatchTab } from './pages/DispatchTab'
import { GateTab } from './pages/GateTab'
import { TerminalTab } from './pages/TerminalTab'

// Routing code-based (pas de codegen) — l'échelle du cockpit ne justifie pas le file-based.
const rootRoute = createRootRoute({ component: AppShell })

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: Landing })

// /$project = layout workspace à onglets (IA option A) ; sa route index = l'onglet Roadmap (home projet).
// Dispatch (V3) + Gate (V4) + Terminal (V5) livrés — l'IA option A est complète.
const projectRoute = createRoute({ getParentRoute: () => rootRoute, path: '/$project', component: ProjectWorkspace })
const roadmapRoute = createRoute({ getParentRoute: () => projectRoute, path: '/', component: RoadmapTab })
const dispatchRoute = createRoute({ getParentRoute: () => projectRoute, path: 'dispatch', component: DispatchTab })
const gateRoute = createRoute({ getParentRoute: () => projectRoute, path: 'gate', component: GateTab })
const terminalRoute = createRoute({ getParentRoute: () => projectRoute, path: 'terminal', component: TerminalTab })

const routeTree = rootRoute.addChildren([
  indexRoute,
  projectRoute.addChildren([roadmapRoute, dispatchRoute, gateRoute, terminalRoute]),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
