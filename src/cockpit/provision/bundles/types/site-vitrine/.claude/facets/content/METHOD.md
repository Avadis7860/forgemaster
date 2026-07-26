# Méthode — facette Content

1. **Schéma d'abord** — une nouvelle famille de contenu = une **content-collection typée**
   (`web/src/content.config.ts`) avant d'écrire une entrée ; `astro check` valide le typage du contenu.
2. **Parité i18n** — toute entrée existe dans les 3 locales (EN/FR/DE) ; les clés d'UI (`web/src/i18n/ui.ts`)
   sont exhaustives par locale. Un trou de traduction = à corriger, jamais à masquer.
3. **Contenu découplé** — jamais de texte en dur dans un layout/composant : le contenu vit dans les collections
   ou les dictionnaires i18n, consommé par la présentation.
4. **SEO éditorial** — `title`/`description` par page et par locale, pensés pour le visiteur et le référencement.
5. **Doc** — après avoir touché la structure de contenu : `docsmap build && docsmap check`. Interroge
   `query(type=tech, scope=mdx)` avant un usage MDX non trivial.
