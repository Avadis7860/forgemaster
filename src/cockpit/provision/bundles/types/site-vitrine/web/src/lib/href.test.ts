// href.test.ts — garde COMPORTEMENTALE (pas source-scan) de la classification de lien. C'est le capital distillé
// du footgun avagency : `mailto:`/`tel:` ne reçoivent JAMAIS `_blank` ni annonce nouvel-onglet, quel que soit
// `external` ; seul un HTTP(S) explicitement externe l'obtient. Tester la logique PURE donne une preuve réelle
// (entrées → sorties), là où scanner la primitive `.astro` ne prouverait qu'une forme de code.
import { describe, expect, it } from 'vitest';
import { classifyHref, schemeOf } from './href';

describe('schemeOf — détection de protocole insensible à la casse', () => {
  it('reconnaît les schémas d’action et web, le reste est « relative »', () => {
    expect(schemeOf('mailto:hi@site.fr')).toBe('mailto');
    expect(schemeOf('MAILTO:HI@SITE.FR')).toBe('mailto');
    expect(schemeOf('tel:+33123')).toBe('tel');
    expect(schemeOf('https://example.com')).toBe('https');
    expect(schemeOf('http://example.com')).toBe('http');
    expect(schemeOf('/fr/contact')).toBe('relative');
    expect(schemeOf('#ancre')).toBe('relative');
    expect(schemeOf('//cdn.example.com')).toBe('relative');
  });
});

describe('classifyHref — le nouvel onglet est réservé au HTTP(S) externe', () => {
  it('mailto/tel : JAMAIS de nouvel onglet, même marqués external', () => {
    for (const href of ['mailto:hello@site.fr', 'tel:+33123456789']) {
      const cls = classifyHref(href, { external: true });
      expect(cls.isNewTab).toBe(false);
      expect(cls.target).toBeUndefined();
      expect(cls.rel).toBeUndefined();
    }
  });

  it('HTTP(S) externe : _blank + rel de durcissement', () => {
    const cls = classifyHref('https://partenaire.example.com', { external: true });
    expect(cls.isNewTab).toBe(true);
    expect(cls.target).toBe('_blank');
    expect(cls.rel).toBe('noopener noreferrer');
  });

  it('HTTP(S) NON marqué external : même onglet', () => {
    expect(classifyHref('https://example.com').isNewTab).toBe(false);
    expect(classifyHref('https://example.com', { external: false }).target).toBeUndefined();
  });

  it('lien relatif : même onglet, quoi qu’on demande', () => {
    expect(classifyHref('/fr/contact', { external: true }).isNewTab).toBe(false);
  });
});
