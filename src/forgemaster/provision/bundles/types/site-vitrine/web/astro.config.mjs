// astro.config.mjs — site vitrine SSG multilingue.
// Stack verrouillée : Astro (statique) + React (îlots) + MDX (contenu) + Tailwind v4 (via @tailwindcss/vite).
// i18n natif : EN primaire (sans préfixe), FR/DE préfixés. `site` est à renseigner par le projet (SEO/canonical).
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import mdx from '@astrojs/mdx';
import tailwindcss from '@tailwindcss/vite';

// Domaine canonique de PRODUCTION (canonical/hreflang/sitemap). Injecté au DÉPLOIEMENT par `SITE_URL`, jamais
// codé en dur : le placeholder `example.com` n'est que le défaut non-déployé. Ne verrouille PAS ce domaine dans
// un test (cf. hygiene.test.ts « SEO honnête ») — sinon la couverture est fausse : test vert, sitemap qui ment.
const site = process.env.SITE_URL ?? 'https://example.com';

export default defineConfig({
  site,
  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'fr', 'de'],
    routing: {
      // EN reste à la racine (`/`), FR/DE sont préfixés (`/fr/`, `/de/`).
      prefixDefaultLocale: false,
    },
  },
  integrations: [react(), mdx()],
  vite: {
    plugins: [tailwindcss()],
  },
});
