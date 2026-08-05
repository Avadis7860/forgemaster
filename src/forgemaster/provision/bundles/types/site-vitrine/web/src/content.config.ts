// content.config.ts — schémas typés des content-collections. Le contenu de la vitrine vit ici (MDX),
// découplé de la présentation et validé au build (`astro check`). Parité i18n : chaque entrée porte sa `locale`.
import { defineCollection } from 'astro:content';
// `z` vient de `astro/zod` depuis Astro 6 : l'export `z` de `astro:content` (et l'alias `astro:schema`)
// y sont dépréciés au profit d'un point d'import unique.
import { z } from 'astro/zod';
import { glob } from 'astro/loaders';

// `sections` : les blocs de contenu d'une page vitrine (hero, features, preuve, CTA…). Génériques : le TEXTE
// réel est propre au projet, le SCHÉMA est stable.
const sections = defineCollection({
  loader: glob({ base: './src/content/sections', pattern: '**/*.mdx' }),
  schema: z.object({
    title: z.string(),
    locale: z.enum(['en', 'fr', 'de']),
    // Ordre d'apparition dans la page (tri déterministe).
    order: z.number().default(0),
    // Résumé optionnel (SEO / aperçu de section).
    summary: z.string().optional(),
    // Brouillon : une entrée en cours de rédaction. Filtrée en PROD par les pages (jamais publiée), visible en
    // dev. C'est le garde-fou contre le contenu inachevé qui atterrit sur une page routée (cf. facette content).
    draft: z.boolean().default(false),
  }),
});

export const collections = { sections };
