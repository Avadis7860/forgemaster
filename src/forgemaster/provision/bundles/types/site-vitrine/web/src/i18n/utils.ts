// utils.ts — helpers i18n purs (testables sans runtime Astro). Dérive la locale de l'URL, expose un traducteur
// à fallback sur `defaultLang`. Aucun texte en dur ailleurs : tout libellé de chrome passe par `useTranslations`.
import { ui, defaultLang } from './ui';

export type Lang = keyof typeof ui;
export type UIKey = keyof (typeof ui)[typeof defaultLang];

/** Locale déduite du 1er segment de chemin (`/fr/…`, `/de/…`) ; défaut = `defaultLang` (EN, non préfixé). */
export function getLangFromUrl(url: URL): Lang {
  const [, seg] = url.pathname.split('/');
  if (seg in ui) return seg as Lang;
  return defaultLang;
}

/** Traducteur pour une locale : retombe sur `defaultLang` si la clé manque dans la locale demandée. */
export function useTranslations(lang: Lang) {
  return function t(key: UIKey): string {
    return ui[lang][key] ?? ui[defaultLang][key];
  };
}

/** Préfixe de chemin d'une locale (`''` pour EN non préfixé, `/fr`/`/de` sinon). Utile aux liens inter-locales. */
export function localePrefix(lang: Lang): string {
  return lang === defaultLang ? '' : `/${lang}`;
}
