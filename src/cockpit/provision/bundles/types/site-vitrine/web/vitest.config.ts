// vitest.config.ts — tests unitaires de la logique (i18n, helpers). La vérif d'écran passe par la boucle
// visuelle (screenshot + Read), pas par vitest. Cœur testable sans runtime Astro.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
});
