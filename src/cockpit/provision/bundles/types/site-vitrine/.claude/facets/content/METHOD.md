# Méthode — facette Content

1. **Schéma d'abord** — une nouvelle famille de contenu = une **content-collection typée**
   (`web/src/content.config.ts`) avant d'écrire une entrée ; `astro check` valide le typage du contenu.
2. **Parité i18n** — toute entrée existe dans les 3 locales (EN/FR/DE) ; les clés d'UI (`web/src/i18n/ui.ts`)
   sont exhaustives par locale. Un trou de traduction = à corriger, jamais à masquer.
3. **Contenu découplé** — jamais de texte en dur dans un layout/composant : le contenu vit dans les collections
   ou les dictionnaires i18n, consommé par la présentation.
4. **SEO éditorial** — `title`/`description` par page et par locale, pensés pour le visiteur et le référencement.
   Le domaine de prod s'injecte au DÉPLOIEMENT (`SITE_URL`), jamais figé dans un test (fausse couverture SEO —
   verrouillé par `web/src/hygiene.test.ts`).
5. **Brouillon, pas placeholder publié** — une entrée inachevée porte `draft: true` (filtrée en prod par les
   pages), jamais un `TODO:`/`lorem ipsum` livré sur une page routée. Verrouillé par `web/src/content/content.test.ts`.
6. **Aucune valeur inventée exécutable** — ne livre JAMAIS une valeur devinée comme commande/URL/clé copiable
   (`git clone …`, endpoint, token). Une valeur externe non connue = **constante de config à placeholder
   explicite** qui fait ÉCHOUER un build de prod tant qu'elle n'est pas renseignée (patron `support`/PayPal de la
   roadmap) — pas une invention plausible qui « a l'air vraie » et casse chez le visiteur.
7. **Aucun claim d'infra non configuré** — n'affirme jamais au visiteur un comportement d'infra absent du dépôt
   (rotation/effacement de logs sans `logging:` réel, chiffrement, rétention). Le contenu légal décrit ce qui EST
   configuré (nginx/compose), pas un vœu.
8. **Doc** — après avoir touché la structure de contenu : `docsmap build && docsmap check`. Interroge
   `query(type=tech, scope=mdx)` avant un usage MDX non trivial.
