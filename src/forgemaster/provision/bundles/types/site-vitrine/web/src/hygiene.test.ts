// hygiene.test.ts — deux disciplines que ni `astro check` ni `astro build` ne voient :
//   1. VIE PRIVÉE — aucun tiers (script externe, traceur) ne se glisse dans le build. La leçon du drain : une
//      garde anti-tiers qui ne balaie que `web/src/**` RATE `astro.config.mjs`, l'endroit le plus probable d'un
//      traceur (intégration analytics). Cette garde inclut donc EXPLICITEMENT la config dans son périmètre.
//   2. SEO HONNÊTE — aucun test ne verrouille un nom de domaine de PROD en dur. Figer `mon-site.fr` dans un test
//      pendant que la config sert un placeholder = fausse couverture : le test est vert, le sitemap ment. Le
//      domaine réel s'injecte au déploiement (`SITE_URL`), il ne se duplique pas dans une assertion.
import { describe, expect, it } from 'vitest';

const SRC = import.meta.glob('./**/*.{astro,ts,tsx,css}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const CONFIG = import.meta.glob('../astro.config.mjs', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

// Le périmètre anti-tiers : le code EXPÉDIÉ (hors tests) ET la config — le trou de la garde du worker était
// justement d'oublier la config.
const SHIPPED = Object.fromEntries(Object.entries(SRC).filter(([path]) => !path.endsWith('.test.ts')));
const SCANNED = { ...SHIPPED, ...CONFIG };

const EXTERNAL_SCRIPT = /<script\b[^>]*\bsrc=["']https?:\/\//i;
const TRACKERS = /google-analytics|googletagmanager|gtag\(|\bhotjar\b|segment\.(?:io|com)\/analytics|mixpanel|facebook\.net\/[^"']*fbevents/i;

describe('vie privée — aucun tiers dans le build', () => {
  it('inclut astro.config.mjs dans son périmètre', () => {
    // L'assertion qui aurait attrapé le trou du worker : la config EST balayée.
    expect(Object.keys(CONFIG)).toContain('../astro.config.mjs');
  });

  it('aucun script externe ni traceur connu, config comprise', () => {
    const offenders = Object.entries(SCANNED)
      .filter(([, source]) => EXTERNAL_SCRIPT.test(source) || TRACKERS.test(source))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it('sait refuser — un snippet analytics en config serait signalé', () => {
    expect(TRACKERS.test("integrations: [/* gtag('config','G-XXX') */]")).toBe(true);
  });
});

describe('SEO honnête — pas de domaine de prod verrouillé en dur', () => {
  // Domaines réservés/non-routables tolérés dans un test (exemples, fixtures) : example.*, *.test, *.invalid,
  // localhost — ils ne prétendent pas être un vrai site.
  const PROD_DOMAIN = /https?:\/\/(?!localhost)(?:[a-z0-9-]+\.)+[a-z]{2,}/gi;
  const RESERVED = /\/\/(?:localhost|(?:[a-z0-9-]+\.)*example\.(?:com|org|net))|\.(?:test|invalid|localhost)\b/i;

  const hardcodedDomains = (source: string): string[] =>
    [...source.matchAll(PROD_DOMAIN)].map(([hit]) => hit).filter((url) => !RESERVED.test(url));

  // Cette garde s'exclut elle-même : ses cas « sait refuser » forgent des URLs de démonstration.
  const TESTS = Object.entries(SRC).filter(
    ([path]) => path.endsWith('.test.ts') && !path.includes('hygiene'),
  );

  it('aucun fichier de test ne code un domaine de prod en dur', () => {
    const offenders = TESTS.flatMap(([path, source]) =>
      hardcodedDomains(source).map((url) => `${path}: ${url}`),
    );
    expect(offenders).toEqual([]);
  });

  it('sait refuser — un vrai domaine est signalé, un exemple réservé non', () => {
    expect(hardcodedDomains("expect(x).toBe('https://mon-site.fr/sitemap.xml')")).toEqual([
      'https://mon-site.fr',
    ]);
    expect(hardcodedDomains("new URL('https://example.com/')")).toEqual([]);
  });
});

describe('contact honnête — pas de lien de contact placeholder PUBLIÉ', () => {
  // Une vitrine de produit gratuit existe pour CONVERTIR : le lien de contact est du capital fonctionnel. Le
  // footgun du drain : shipper un CTA de contact resté à l'ébauche (`mailto:you@example.com`, un profil social
  // pointant sur la RACINE nue de la plateforme) — le visiteur clique dans le vide, la machine ne tourne pas.
  // `astro build` ne voit pas qu'un href est un placeholder. Cette garde généralise le trou des « liens externes »
  // aux CTA de contact ; haute précision (domaines réservés RFC 2606 + racines de plateforme nues), zéro faux
  // positif sur un vrai contact (`mailto:contact@mon-agence.fr`, un profil social avec un chemin réel).
  //
  // On lit le code EXPÉDIÉ débarrassé de ses commentaires : un exemple en prose (comme dans lib/href.ts) ne doit
  // pas être compté comme un lien rendu.
  const stripComments = (s: string): string =>
    s.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^\s*\/\/.*$/gm, ' ').replace(/<!--[\s\S]*?-->/g, ' ');

  // mailto/tel vers un domaine réservé (example.*, .test, .invalid) OU un local-part manifestement factice.
  const PLACEHOLDER_MAILTO =
    /(?:mailto|tel):[^"'\s>)]*(?:@(?:(?:[a-z0-9-]+\.)*example\.(?:com|org|net)|[a-z0-9-]+\.(?:test|invalid))|(?:^|:)(?:you|your|your-?email|youremail|email|changeme|votre-?email)@)/i;

  // Racine NUE d'une plateforme sociale connue (aucun chemin de profil) = lien pas encore renseigné.
  const BARE_SOCIAL =
    /https?:\/\/(?:www\.)?(?:linkedin|twitter|x|github|instagram|facebook|youtube|tiktok)\.com\/?(?=["'\s>)]|$)/gi;

  const placeholderLinks = (source: string): string[] => {
    const clean = stripComments(source);
    const hits: string[] = [];
    for (const m of clean.matchAll(/(?:mailto|tel):[^"'\s>)]+/gi)) {
      if (PLACEHOLDER_MAILTO.test(m[0])) hits.push(m[0]);
    }
    for (const m of clean.matchAll(BARE_SOCIAL)) hits.push(m[0]);
    return hits;
  };

  it('aucun fichier expédié ne rend un lien de contact placeholder', () => {
    const offenders = Object.entries(SHIPPED).flatMap(([path, source]) =>
      placeholderLinks(source).map((hit) => `${path}: ${hit}`),
    );
    expect(offenders).toEqual([]);
  });

  it('sait refuser — un CTA placeholder est signalé, un vrai contact ne l’est pas', () => {
    expect(placeholderLinks('<a href="mailto:you@example.com">Écrire</a>')).toHaveLength(1);
    expect(placeholderLinks('<a href="https://linkedin.com">LinkedIn</a>')).toHaveLength(1);
    expect(placeholderLinks('<a href="mailto:contact@mon-agence.fr">Écrire</a>')).toEqual([]);
    expect(placeholderLinks('<a href="https://linkedin.com/in/vrai-profil">LinkedIn</a>')).toEqual([]);
    expect(placeholderLinks('<a href="tel:+33123456789">Appeler</a>')).toEqual([]);
  });
});
