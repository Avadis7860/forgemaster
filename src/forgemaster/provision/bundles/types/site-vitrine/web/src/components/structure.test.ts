// structure.test.ts — garde de VALIDITÉ STRUCTURELLE du HTML rendu, semée par le bundle site-vitrine.
// `astro check` prouve les types, `astro build` prouve que ça compile ; NI l'un ni l'autre ne voit qu'un
// heading (`h1`–`h6`) a été imbriqué dans un élément de CONTENU DE PHRASE (`span`, `a`, `em`…). C'est du HTML
// invalide : un heading est du flow content, interdit comme descendant d'un phrasing element. Le navigateur ne
// lève pas d'erreur — il REMONTE le heading hors du span au parsing, ce qui casse silencieusement la structure
// (le style scopé sur le wrapper saute, l'ordre du document dérive). C'est exactement le défaut `TexturedTitle`
// du drain avagency (`<Tag as="h1">` posé dans un `<span class="tt-wrap">`). Cette garde le rend INJOUABLE.
//
// Elle est GÉNÉRIQUE (aucun composant/token de projet en dur) : elle scanne le template de CHAQUE fichier semé
// ou écrit par le worker, résout les headings LITTÉRAUX (`<h2>`) ET DYNAMIQUES (`<Tag>` quand la prop `as` du
// composant admet un heading), et maintient une pile de wrappers de phrase. Même stratégie que les autres
// gardes : lecture brute (`import.meta.glob(?raw)`), aucun runtime Astro ni navigateur.
import { describe, expect, it } from 'vitest';

const COMPONENTS = import.meta.glob('./*.astro', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const SHELL = import.meta.glob('../{layouts,pages}/**/*.astro', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const ALL: Record<string, string> = { ...COMPONENTS, ...SHELL };

// Éléments de CONTENU DE PHRASE qui NE peuvent PAS contenir un heading (liste HTML5, cas courants d'une vitrine).
const PHRASING = new Set([
  'span', 'a', 'b', 'i', 'em', 'strong', 'small', 's', 'u', 'mark', 'sub', 'sup', 'abbr', 'cite',
  'q', 'code', 'kbd', 'samp', 'var', 'time', 'label', 'output', 'bdi', 'bdo', 'data', 'dfn', 'wbr',
]);

// Balises vides (void) : ouvrent sans jamais empiler.
const VOID = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr',
]);

const HEADING = /^h[1-6]$/;

/** Le template d'un `.astro` = tout ce qui suit le frontmatter (`---`…`---`), purgé des blocs style/script et
 *  des commentaires HTML/JS — pour qu'un nom de balise cité en prose n'empoisonne pas le scan (test vert faux). */
function template(source: string): string {
  let body = source.replace(/^---[\s\S]*?\n---\n?/, '');
  body = body.replace(/<style[\s\S]*?<\/style>/gi, ' ').replace(/<script[\s\S]*?<\/script>/gi, ' ');
  body = body.replace(/<!--[\s\S]*?-->/g, ' ').replace(/\/\*[\s\S]*?\*\//g, ' ');
  return body;
}

/** Le(s) nom(s) de balise dynamique de ce fichier qui peuvent rendre un heading : une prop `as` dont le type
 *  admet un `h1`–`h6`, capturée par son alias de déstructuration (`const { as: Tag = 'h2' }`). Sans alias, le
 *  nom local est `as` (rare en Astro, mais couvert). */
function dynamicHeadingTags(source: string): Set<string> {
  const names = new Set<string>();
  const frontmatter = source.match(/^---([\s\S]*?)\n---/)?.[1] ?? '';
  const asType = frontmatter.match(/\bas\s*\??\s*:\s*([^;]+)/)?.[1] ?? '';
  const admitsHeading = /['"]h[1-6]['"]/.test(asType);
  if (!admitsHeading) return names;
  const alias = frontmatter.match(/\bas\s*:\s*([A-Z][A-Za-z0-9_]*)/)?.[1];
  names.add(alias ?? 'as');
  return names;
}

/** Scanne un template et renvoie les headings ouverts alors qu'un élément de phrase est ouvert (offenders). */
function headingsInsidePhrasing(source: string): string[] {
  const dyn = dynamicHeadingTags(source);
  const body = template(source);
  const stack: string[] = [];
  const offenders: string[] = [];
  // Balises HTML : `<tag …>` (ouvrante), `</tag>` (fermante), auto-fermante `<tag …/>`.
  const TAG = /<(\/?)([A-Za-z][A-Za-z0-9]*)\b([^>]*?)(\/?)>/g;
  for (const m of body.matchAll(TAG)) {
    const [, closing, rawName, attrs, selfClose] = m;
    const name = rawName;
    const isHeading = HEADING.test(name.toLowerCase()) || dyn.has(name);
    if (closing) {
      // Ferme le wrapper de phrase correspondant, s'il est sur la pile.
      const idx = stack.lastIndexOf(name.toLowerCase());
      if (idx !== -1) stack.splice(idx, 1);
      continue;
    }
    if (isHeading && stack.length > 0) {
      offenders.push(`<${name}> sous <${stack[stack.length - 1]}>`);
    }
    // Empile uniquement les éléments de phrase NON auto-fermants et non-void (ceux qui peuvent envelopper).
    const lname = name.toLowerCase();
    if (!selfClose && !VOID.has(lname) && PHRASING.has(lname)) stack.push(lname);
  }
  return offenders;
}

describe('les sources sont bien lues — aucun test ne passe sur du vide', () => {
  it('trouve des composants semés à scanner', () => {
    expect(Object.keys(COMPONENTS).length).toBeGreaterThanOrEqual(1);
    expect(Object.entries(ALL).filter(([, s]) => s.trim() === '')).toEqual([]);
  });
});

describe('validité structurelle — aucun heading imbriqué dans un élément de phrase', () => {
  it('aucun fichier semé/worker ne pose un heading (littéral ou dynamique) sous un span/a/em…', () => {
    const offenders = Object.entries(ALL)
      .flatMap(([path, source]) => headingsInsidePhrasing(source).map((o) => `${path}: ${o}`));
    expect(offenders).toEqual([]);
  });

  it('sait refuser — le défaut TexturedTitle du drain (heading dynamique sous span) est signalé', () => {
    // Reproduit le nesting invalide corrigé : un `<Tag>` piloté par `as: 'h1'|'h2'` posé dans un `<span>`.
    const forged = [
      '---',
      "interface Props { as?: 'h1' | 'h2' | 'h3'; }",
      "const { as: Tag = 'h2' } = Astro.props;",
      '---',
      '<span class="wrap"><Tag><slot /></Tag></span>',
    ].join('\n');
    expect(headingsInsidePhrasing(forged)).toHaveLength(1);
  });

  it('sait refuser — un heading LITTÉRAL sous <a> est signalé', () => {
    expect(headingsInsidePhrasing('---\n---\n<a href="/"><h2>Titre</h2></a>')).toHaveLength(1);
  });

  it('reste vert quand c’est correct — heading dans un <div>, ou span frère du heading', () => {
    expect(headingsInsidePhrasing('---\n---\n<div class="wrap"><h2>Titre</h2></div>')).toEqual([]);
    expect(headingsInsidePhrasing('---\n---\n<div><span aria-hidden="true"></span><h2>Ok</h2></div>')).toEqual([]);
  });
});
