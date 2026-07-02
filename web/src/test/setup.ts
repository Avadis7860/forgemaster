import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Démonte l'arbre React entre chaque test (JSDOM partagé) — pas de fuite d'état d'un test à l'autre.
afterEach(() => cleanup())
