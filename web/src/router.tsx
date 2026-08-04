import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AppShell } from './App'
import { Landing } from './pages/Landing'
import { Bundles } from './pages/Bundles'
import { Capital } from './pages/Capital'
import { Templates } from './pages/Templates'
import { Git } from './pages/Git'
import { ProjectWorkspace } from './pages/ProjectWorkspace'
import { AccueilTab } from './pages/AccueilTab'
import { RoadmapTab } from './pages/RoadmapTab'
import { TravailTab } from './pages/TravailTab'
import { OpsTab } from './pages/OpsTab'
import { SettingsTab } from './pages/SettingsTab'
import { SetupWizard } from './pages/SetupWizard'

// Routing code-based (pas de codegen) — l'échelle du forgemaster ne justifie pas le file-based.
const rootRoute = createRootRoute({ component: AppShell })

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: Landing })

// Explorers de ressources GLOBALES (pas l'état d'un projet), promus du Landing en destinations propres
// atteintes depuis le rail gauche (catégories `bundle` / `capital-token`). Chacun reste piloté par l'URL.
const bundlesRoute = createRoute({ getParentRoute: () => rootRoute, path: '/bundles', component: Bundles })
const capitalRoute = createRoute({ getParentRoute: () => rootRoute, path: '/capital', component: Capital })
const templatesRoute = createRoute({ getParentRoute: () => rootRoute, path: '/templates', component: Templates })
// /git = surface Git de plein droit (ex-drawer Ops `?panel=git`), ressource globale atteinte depuis le rail.
// Per-projet via son propre sélecteur (`?project=`) — cf. GitExplorer. Pilotée par l'URL (deep-linkable).
const gitRoute = createRoute({ getParentRoute: () => rootRoute, path: '/git', component: Git })

// /setup = wizard 1er-démarrage guidé (bienvenue → coffre → 1er projet → miroir+token → prêt).
const setupRoute = createRoute({ getParentRoute: () => rootRoute, path: '/setup', component: SetupWizard })

// /settings = onboarding self-hosted (instance-level, hors projet) : coffre de secrets + token par repo.
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/settings', component: SettingsTab })

// /$project = layout workspace à onglets. Refonte IA v3 (8 onglets plats → 3 + accueil) : sa route index = l'
// **Accueil** (Docs FONDU dedans, P4) ; Dispatch + Gate fusionnés en « Travail » (P2) ; Git+Runtime+Flow+Terminal
// regroupés en « Ops » (P3 : Terminal ancre + Runtime fondu + Git drawer + Flow modal, overlays via `?panel=`).
const projectRoute = createRoute({ getParentRoute: () => rootRoute, path: '/$project', component: ProjectWorkspace })
const accueilRoute = createRoute({ getParentRoute: () => projectRoute, path: '/', component: AccueilTab })
const roadmapRoute = createRoute({ getParentRoute: () => projectRoute, path: 'roadmap', component: RoadmapTab })
const travailRoute = createRoute({ getParentRoute: () => projectRoute, path: 'travail', component: TravailTab })
const opsRoute = createRoute({ getParentRoute: () => projectRoute, path: 'ops', component: OpsTab })

const routeTree = rootRoute.addChildren([
  indexRoute,
  bundlesRoute,
  capitalRoute,
  templatesRoute,
  gitRoute,
  setupRoute,
  settingsRoute,
  projectRoute.addChildren([accueilRoute, roadmapRoute, travailRoute, opsRoute]),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
