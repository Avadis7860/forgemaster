// utils.test.ts — vérifie les helpers i18n purs (une capacité livrée = un test). Le worker garde ce test vert
// et l'étend quand il ajoute des locales/clés.
import { describe, it, expect } from 'vitest';
import { getLangFromUrl, useTranslations, localePrefix } from './utils';

describe('getLangFromUrl', () => {
  it('défaut EN à la racine (non préfixé)', () => {
    expect(getLangFromUrl(new URL('https://example.com/'))).toBe('en');
  });
  it('détecte FR et DE au 1er segment', () => {
    expect(getLangFromUrl(new URL('https://example.com/fr/'))).toBe('fr');
    expect(getLangFromUrl(new URL('https://example.com/de/page'))).toBe('de');
  });
  it('segment inconnu → défaut EN', () => {
    expect(getLangFromUrl(new URL('https://example.com/es/'))).toBe('en');
  });
});

describe('useTranslations', () => {
  it('traduit dans la locale demandée', () => {
    expect(useTranslations('fr')('nav.home')).toBe('Accueil');
    expect(useTranslations('de')('nav.home')).toBe('Startseite');
  });
  it('retombe sur EN si la locale demandée est EN', () => {
    expect(useTranslations('en')('footer.rights')).toBe('All rights reserved.');
  });
});

describe('localePrefix', () => {
  it('EN = préfixe vide, FR/DE = préfixés', () => {
    expect(localePrefix('en')).toBe('');
    expect(localePrefix('fr')).toBe('/fr');
    expect(localePrefix('de')).toBe('/de');
  });
});
