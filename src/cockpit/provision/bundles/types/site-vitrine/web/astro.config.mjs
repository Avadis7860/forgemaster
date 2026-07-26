// astro.config.mjs — site vitrine SSG multilingue.
// Stack verrouillée : Astro (statique) + React (îlots) + MDX (contenu) + Tailwind v4 (via @tailwindcss/vite).
// i18n natif : EN primaire (sans préfixe), FR/DE préfixés. `site` est à renseigner par le projet (SEO/canonical).
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import mdx from '@astrojs/mdx';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  // À renseigner par le projet : l'URL canonique de production (sert les balises canonical/hreflang + sitemap).
  site: 'https://example.com',
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
