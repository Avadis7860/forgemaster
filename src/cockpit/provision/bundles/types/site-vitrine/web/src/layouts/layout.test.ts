// layout.test.ts — LA garde du chrome (socle a11y), semée par le bundle site-vitrine. `astro check` prouve les
// types, `astro build` prouve que ça rend ; ni l'un ni l'autre ne voit ce qui rend une enveloppe ACCESSIBLE.
// Cette garde relit les fichiers réels comme texte brut (`import.meta.glob(?raw)`), sans runtime Astro ni
// navigateur — même stratégie que les autres gardes de ce dépôt.
//
// Elle est GÉNÉRIQUE (aucun token/marque/helper de projet en dur) : verte sur le socle semé ET sur une
// enveloppe worker correcte. Elle verrouille sept invariants que le worker, en étendant le chrome, casse
// typiquement un à un (la « churn » de re-dérivation) :
//   1. landmarks uniques (un seul banner/main/contentinfo par page) ;
//   2. skip-link réel (premier focusable, vise l'id que <main> porte, déplace vraiment le focus) ;
//   3. navigations nommées et distinctes ;
//   4. `lang` suit la locale rendue, pas une constante ;
//   5. aucun texte visiteur écrit en dur dans le layout ;
//   6. cibles tactiles ≥44px ;
//   7. contraste : aucun texte lisible peint en dégradé, header collant compensé, un seul marqueur de page.
// Les invariants 7 sont les trois trous récurrents du drain (contraste masque-dégradé, header collant sans
// scroll-margin, double aria-current) — verrouillés ici pour de bon.
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const LAYOUTS = import.meta.glob('./*.astro', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const PAGES = import.meta.glob('../pages/**/*.astro', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

function sourceOf(files: Record<string, string>, path: string): string {
  const source = files[path];
  if (source === undefined) throw new Error(`fichier manquant : ${path}`);
  return source;
}

const BASE = sourceOf(LAYOUTS, './BaseLayout.astro');

// La CSS est lue du DISQUE (node:fs), pas via import.meta.glob : le plugin Tailwind v4 avale le `?raw` d'un glob
// CSS (contenu vide), alors que `.astro` passe. En environnement `node` (cf. vitest.config), fs est fiable.
const STYLES_DIR = fileURLToPath(new URL('../styles/', import.meta.url));
const CSS = readdirSync(STYLES_DIR)
  .filter((name) => name.endsWith('.css'))
  .map((name) => readFileSync(STYLES_DIR + name, 'utf8'))
  .join('\n');

/** Tous les fichiers que le build transforme en HTML — les layouts et les routes ensemble. */
const SHELL = { ...LAYOUTS, ...PAGES };

/**
 * Une source débarrassée de ses commentaires. La prose porte des apostrophes et des noms de classe qui
 * empoisonneraient, silencieusement et dans un test vert, un scan qui lit le fichier comme du code.
 */
function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^\s*\/\/.*$/gm, ' ');
}

/**
 * Nombre de balises HTML ouvrantes `<tag` dans une source : `<mainstream>` ne doit pas compter, ni une balise
 * NOMMÉE dans un commentaire. Lit le code débarrassé de ses commentaires.
 */
function opens(source: string, tag: string): number {
  return (code(source).match(new RegExp(`<${tag}(?=[\\s/>])`, 'gi')) ?? []).length;
}

/** Toute chaîne entre quotes d'une source — là où vit une liste de classes, inline ou derrière un const. */
function quotedStrings(source: string): string[] {
  return [...code(source).matchAll(/'([^']*)'|"([^"]*)"|`([^`]*)`/g)].map(
    ([, s, d, b]) => s ?? d ?? b ?? '',
  );
}

describe('les sources sont bien lues — aucun test ne passe sur du vide', () => {
  it('trouve le layout socle, au moins une route et les styles', () => {
    // Un dossier renommé ou un glob muet rendrait chaque assertion en dessous vacuously vraie.
    expect(Object.keys(LAYOUTS)).toContain('./BaseLayout.astro');
    expect(Object.keys(PAGES).length).toBeGreaterThanOrEqual(1);
    expect(Object.entries(SHELL).filter(([, source]) => source.trim() === '')).toEqual([]);
    expect(CSS.trim().length).toBeGreaterThan(0);
  });
});

describe('landmarks — un seul de chaque par page', () => {
  const SINGLETONS = ['header', 'main', 'footer'] as const;

  it.each(SINGLETONS)('BaseLayout ouvre un seul <%s>', (tag) => {
    expect(opens(BASE, tag)).toBe(1);
  });

  it.each(SINGLETONS)('aucune route ni autre layout n’ouvre de <%s> concurrent', (tag) => {
    // Une page qui ouvre son propre <header> donnerait au document rendu deux landmarks `banner`.
    const offenders = Object.entries(SHELL)
      .filter(([path]) => path !== './BaseLayout.astro')
      .filter(([, source]) => opens(source, tag) > 0)
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it('sait compter — le détecteur ne se laisse pas berner par un nom de balise plus long', () => {
    expect(opens('<main id="main"><mainstream/></main>', 'main')).toBe(1);
    expect(opens('<header><header>', 'header')).toBe(2);
  });

  it('donne au contenu le landmark main, pas un simple conteneur', () => {
    expect(BASE).toMatch(/<main\b[^>]*>/);
    expect(BASE).toMatch(/<slot\s*\/>/);
  });
});

describe('skip-link — le premier arrêt du clavier', () => {
  /** Le body, à partir de sa balise ouvrante — ce que le clavier parcourt. */
  const BODY = BASE.slice(BASE.indexOf('<body'));

  it('est le premier élément focusable du body et vise #main', () => {
    // Placé en second, il serait atteint APRÈS les liens mêmes qu'il existe pour sauter.
    const firstAnchor = BODY.indexOf('<a');
    expect(firstAnchor).toBeGreaterThan(-1);
    expect(BODY.slice(firstAnchor, firstAnchor + 200)).toMatch(/href="#main"/);
    const firstButton = BODY.search(/<(?:button|input|select|textarea)\b/);
    if (firstButton > -1) expect(firstButton).toBeGreaterThan(firstAnchor);
  });

  it('vise l’id que <main> porte réellement', () => {
    // Le skip-link mort classique : l'ancre dit `#content`, le landmark dit `id="main"`.
    const target = BASE.match(/href="#([\w-]+)"/)?.[1];
    expect(target).toBeDefined();
    expect(BASE).toMatch(new RegExp(`<main\\b[^>]*id="${target}"`));
  });

  it('déplace vraiment le focus — <main> est focusable par programme (tabindex=-1)', () => {
    // Sans `tabindex="-1"`, WebKit défile jusqu'à l'ancre mais laisse le focus dans l'entête : le Tab suivant
    // reparcourt la navigation, exactement ce que le visiteur a demandé à sauter.
    expect(BASE).toMatch(/<main\b[^>]*tabindex="-1"/);
  });

  it('se montre quand il reçoit le focus', () => {
    // Caché hors flux (`sr-only`, ou parqué par un transform), ramené par une variante `focus:`. Un skip-link
    // qui ne redevient jamais visible est invisible au clavier voyant — SC 2.4.7.
    const link = BASE.match(/<a\b[^>]*href="#main"[\s\S]*?>/)?.[0] ?? '';
    expect(link).toMatch(/focus(?:-visible)?:/);
  });
});

describe('navigations — nommées, et distinctement', () => {
  const TAGS = [...BASE.matchAll(/<nav\b[^>]*>/g)].map(([tag]) => tag);

  it('en pose au moins une', () => {
    expect(TAGS.length).toBeGreaterThanOrEqual(1);
  });

  it('chacune porte un nom accessible', () => {
    const unnamed = TAGS.filter((tag) => !/aria-label(?:ledby)?=/.test(tag));
    expect(unnamed).toEqual([]);
  });

  it('aucune ne reprend le nom d’une autre', () => {
    // Deux landmarks nommés pareil sont deux landmarks qu'un lecteur d'écran ne peut distinguer.
    const names = TAGS.map((tag) => tag.match(/aria-label=\{?([^}\s>]+)/)?.[1]);
    expect(new Set(names).size).toBe(names.length);
  });

  it('tire ces noms d’un traducteur, jamais d’une chaîne littérale écrite ici', () => {
    // `aria-label="Main navigation"` expédierait de l'anglais à la page allemande.
    const literal = TAGS.filter((tag) => /aria-label="/.test(tag));
    expect(literal).toEqual([]);
  });
});

describe('lang — la locale rendue, pas une constante', () => {
  it('lie <html lang> à une valeur calculée', () => {
    expect(BASE).toMatch(/<html\b[^>]*lang=\{/);
    // La faute que ça interdit : `<html lang="en">` sur /de/, annoncé en anglais par tout lecteur d'écran.
    expect(BASE).not.toMatch(/<html\b[^>]*lang="[a-z]{2}"/);
  });
});

describe('aucun texte visiteur écrit en dur dans le layout', () => {
  it('ne rend que des libellés traduits ou venus du contenu', () => {
    // Chaque nœud de texte du chrome est `{t('…')}`, `{data.label}`… Une phrase écrite ici partirait
    // non traduite vers les trois locales. (Les nœuds `{…}` commencent par `{`, donc non capturés.)
    const rendered = code(BASE).slice(code(BASE).indexOf('<body'));
    const textNodes = [...rendered.matchAll(/>\s*([A-Za-zÀ-ÿ][^<>{}]{3,})\s*</g)].map(([, text]) =>
      text.trim(),
    );
    expect(textNodes).toEqual([]);
  });
});

describe('cibles tactiles — 44px sur les liens de chrome', () => {
  // `min-h-11` = 2.75rem = 44px (SC 2.5.8). Lu depuis les listes de classes elles-mêmes — inline ou derrière un
  // const — pour qu'un lien de chrome ajouté plus tard soit contrôlé aussi, au lieu d'un spot-check figé.
  const touchable = (source: string): string[] =>
    quotedStrings(source).filter((value) => /\binline-flex\b/.test(value));

  it('tout lien inline-flex du chrome fait au moins 44px de haut', () => {
    expect(touchable(BASE).filter((value) => !/\bmin-h-11\b/.test(value))).toEqual([]);
  });

  it('sait refuser — un inline-flex sans min-h-11 est signalé', () => {
    // Sans ce cas, la suite ne prouverait que le pass d'aujourd'hui, jamais qu'elle sait dire non.
    const forged = "const a = 'inline-flex items-center text-body';";
    expect(touchable(forged).filter((value) => !/\bmin-h-11\b/.test(value))).toHaveLength(1);
  });
});

describe('contraste — aucun texte lisible peint en dégradé', () => {
  // Un texte « clippé » sur un dégradé (`background-clip:text`, utilitaire `text-gradient-*`) a un contraste
  // VARIABLE le long du dégradé : ses arrêts sombres tombent typiquement sous le plancher lisible (3:1 grand
  // texte, 4.5:1 corps — SC 1.4.3). Le mot-marque en est la tentation classique et le trou récurrent du drain.
  // La couleur d'un texte lisible doit être un aplat au-dessus du plancher. Décoratif (`bg-gradient-*` sur un
  // trait `aria-hidden`) reste permis — c'est le préfixe `text-` qui peint du texte lisible.
  const GRADIENT_TEXT = /(?<![\w-])text-gradient-[a-z-]+/g;

  it('le chrome n’utilise aucun `text-gradient-*` sur du texte', () => {
    const hits = [...code(BASE).matchAll(GRADIENT_TEXT)].map(([hit]) => hit);
    expect(hits).toEqual([]);
  });

  it('sait refuser — `text-gradient-*` détecté, `bg-gradient-*` (décoratif) ignoré', () => {
    expect('class="text-gradient-copper"'.match(GRADIENT_TEXT)).not.toBeNull();
    expect('class="bg-gradient-copper"'.match(GRADIENT_TEXT)).toBeNull();
  });
});

describe('header collant — la cible du skip-link n’est pas masquée', () => {
  // Un header `sticky`/`fixed` recouvre le haut du viewport ; sauter à `#main` (ou à une ancre) amène la cible
  // SOUS l'entête, hors de vue — sauf compensation par `scroll-margin-top`/`scroll-padding-top` (classe Tailwind
  // `scroll-mt-*`/`scroll-pt-*` ou CSS). Sans élément collant, la règle ne s'applique pas (vrai à vide, documenté).
  const classAttrs = quotedStrings(BASE).join(' ');
  const hasSticky = /\b(?:sticky|fixed)\b/.test(classAttrs);
  const compensated =
    /\bscroll-(?:mt|pt)-\S/.test(classAttrs) || /scroll-(?:margin|padding)-top/.test(CSS);

  it('un chrome qui colle un élément compense le défilement des ancres', () => {
    if (hasSticky) expect(compensated).toBe(true);
    else expect(hasSticky).toBe(false);
  });
});

describe('page courante — un seul marqueur, et dans la navigation', () => {
  // `aria-current="page"` marque l'item courant D'UNE NAVIGATION. Un lien HORS `<nav>` (le mot-marque/logo, qui
  // pointe aussi vers l'accueil) qui porte ce marqueur le DOUBLE sur la page d'accueil et le met au mauvais
  // endroit : la marque de page appartient au lien de nav, pas au logo. (`aria-current="true"` d'un widget —
  // sélecteur de langue — n'est PAS visé : ce n'est pas une navigation de page.)
  const navRegionsOf = (src: string): Array<[number, number]> =>
    [...src.matchAll(/<nav\b[\s\S]*?<\/nav>/g)].map((m) => [m.index!, m.index! + m[0].length]);

  const currentPageOutsideNav = (src: string): string[] => {
    const clean = code(src);
    const regions = navRegionsOf(clean);
    const inNav = (i: number): boolean => regions.some(([a, b]) => i >= a && i < b);
    return [...clean.matchAll(/<a\b[^>]*aria-current[^>]*>/g)]
      .filter((m) => /["']page["']/.test(m[0]))
      .filter((m) => !inNav(m.index!))
      .map((m) => m[0].slice(0, 60));
  };

  it('aucun lien hors <nav> ne dérive aria-current="page"', () => {
    expect(currentPageOutsideNav(BASE)).toEqual([]);
  });

  it('sait refuser — un logo hors nav marqué page est signalé, le lien de nav ne l’est pas', () => {
    const forged =
      '<a href="/" aria-current="page">Logo</a><nav><a aria-current="page">Home</a></nav>';
    expect(currentPageOutsideNav(forged)).toHaveLength(1);
  });
});

describe('hreflang — vers des routes qui existent', () => {
  it('émet des alternates hreflang ancrés sur le site configuré', () => {
    // Le piège (corrigé) : dériver le hreflang du CHEMIN courant pour toutes les locales pointe vers
    // /fr/<page> qui n'existe pas → 404. Le socle mappe des racines de locale, ancrées sur `Astro.site`.
    expect(BASE).toMatch(/rel="alternate"/);
    expect(BASE).toMatch(/hreflang=/);
    expect(BASE).toMatch(/Astro\.site/);
  });
});
