import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

// Déterminisme du gate front : les surfaces à rendu lazy (DocView/HighlightedCode via `import()` +
// Suspense — RepoExplorer, Bundles, Docs) résolvent leur chunk en async. Le `findBy` par défaut de
// Testing-Library plafonne à 1000 ms : sous la contention CPU de la SUITE COMPLÈTE, le chunk peut
// dépasser cette fenêtre → faux-rouge intermittent (jamais en isolation). On relève le plafond
// d'attente à 5000 ms : ce n'est qu'un maximum (un `findBy` résout dès que l'élément apparaît, donc
// zéro ralentissement sur un run vert), qui absorbe la latence de chargement de chunk sous charge.
configure({ asyncUtilTimeout: 5000 })

// Démonte l'arbre React entre chaque test (JSDOM partagé) — pas de fuite d'état d'un test à l'autre.
afterEach(() => cleanup())
