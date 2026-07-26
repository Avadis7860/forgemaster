# web/ — l'app Astro du site vitrine

L'app statique (Astro SSG). Le cadre de stack et les conventions vivent dans le `CLAUDE.md` racine et
`docs/architecture.md` — lis-les avant de structurer. Le groupe de gate `front` se déclenche sur ce chemin
(`web/`).

## Contenu

- **`astro.config.mjs`** — stack + i18n (EN/FR/DE). **`src/pages/`** — routes par locale. **`src/layouts/`** —
  `BaseLayout` (SEO/méta/a11y). **`src/components/`** — îlots React (hydratés à la demande). **`src/content/`** —
  contenu MDX typé (content-collections). **`src/styles/`** — design tokens Tailwind. **`src/i18n/`** —
  dictionnaires + helpers.

## Règle

Zéro-JS par défaut (hydrate un îlot seulement s'il est interactif) ; contenu découplé & typé ; parité i18n
EN/FR/DE ; a11y (wai-aria-apg) + SEO comme acceptance. Le gate composite (`astro check → tsc → vitest → astro
build`) doit rester vert avant tout commit. Interroge `frontmap`/`codemap` avant de `grep`/lire en bloc.
