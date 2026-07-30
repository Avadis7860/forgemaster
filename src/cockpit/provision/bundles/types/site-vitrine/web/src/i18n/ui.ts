// ui.ts — dictionnaires d'UI (labels de chrome : nav, a11y, pied de page). Le CONTENU éditorial vit dans les
// content-collections ; ici uniquement les libellés d'interface, en parité EN/FR/DE. Une clé manquante dans une
// locale retombe sur `defaultLang` (voir utils.ts) — à combler, jamais à masquer.
export const languages = {
  en: 'English',
  fr: 'Français',
  de: 'Deutsch',
} as const;

export const defaultLang = 'en';

export const ui = {
  en: {
    'nav.home': 'Home',
    'nav.skipToContent': 'Skip to content',
    'nav.menu': 'Menu',
    'footer.rights': 'All rights reserved.',
    'lang.switch': 'Change language',
    'a11y.opensNewTab': 'opens in a new tab',
  },
  fr: {
    'nav.home': 'Accueil',
    'nav.skipToContent': 'Aller au contenu',
    'nav.menu': 'Menu',
    'footer.rights': 'Tous droits réservés.',
    'lang.switch': 'Changer de langue',
    'a11y.opensNewTab': 'ouvre un nouvel onglet',
  },
  de: {
    'nav.home': 'Startseite',
    'nav.skipToContent': 'Zum Inhalt springen',
    'nav.menu': 'Menü',
    'footer.rights': 'Alle Rechte vorbehalten.',
    'lang.switch': 'Sprache wechseln',
    'a11y.opensNewTab': 'öffnet in einem neuen Tab',
  },
} as const;
