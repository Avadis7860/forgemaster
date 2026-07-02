import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AppShell } from './App'
import { Landing } from './pages/Landing'
import { ProjectWorkspace } from './pages/ProjectWorkspace'
import { RoadmapTab } from './pages/RoadmapTab'

// Routing code-based (pas de codegen) — l'échelle du cockpit ne justifie pas le file-based.
const rootRoute = createRootRoute({ component: AppShell })

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: Landing })

// /$project = layout workspace à onglets (IA option A) ; sa route index = l'onglet Roadmap (home projet).
// Dispatch/Gate/Terminal deviendront des routes enfants dans leurs vagues (V3/V4/V5).
const projectRoute = createRoute({ getParentRoute: () => rootRoute, path: '/$project', component: ProjectWorkspace })
const roadmapRoute = createRoute({ getParentRoute: () => projectRoute, path: '/', component: RoadmapTab })

const routeTree = rootRoute.addChildren([
  indexRoute,
  projectRoute.addChildren([roadmapRoute]),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
