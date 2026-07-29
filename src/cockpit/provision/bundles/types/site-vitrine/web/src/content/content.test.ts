// content.test.ts — garde de discipline du contenu routé. `astro check` valide le TYPAGE du contenu ; il ne voit
// pas qu'un `TODO:` ou un « lorem ipsum » a été PUBLIÉ sur une page. Cette garde relit les entrées de collection
// comme texte brut et refuse tout marqueur de brouillon dans une entrée NON-draft — une entrée `draft: true` est
// un travail en cours, filtré en prod par les pages (elle a le droit d'être inachevée).
import { describe, expect, it } from 'vitest';

const SECTIONS = import.meta.glob('./sections/**/*.mdx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

/** Sépare le frontmatter (entre les deux premiers `---`) du corps rendu. */
function frontmatterAndBody(source: string): { frontmatter: string; body: string } {
  const m = source.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return { frontmatter: '', body: source };
  return { frontmatter: m[1], body: m[2] };
}

function isDraft(frontmatter: string): boolean {
  return /^\s*draft\s*:\s*true\s*$/m.test(frontmatter);
}

/** Marqueurs de contenu inachevé — choisis pour ne pas heurter de la prose légitime. */
const PLACEHOLDER = /\bTODO\b|\bFIXME\b|\bXXX\b|lorem ipsum/i;

describe('contenu routé — pas de brouillon publié', () => {
  it('lit bien les sections — aucun test ne passe sur du vide', () => {
    expect(Object.keys(SECTIONS).length).toBeGreaterThanOrEqual(1);
  });

  it('aucune entrée publiée (non-draft) ne porte de marqueur de placeholder', () => {
    const offenders = Object.entries(SECTIONS)
      .map(([path, source]) => ({ path, ...frontmatterAndBody(source) }))
      .filter(({ frontmatter }) => !isDraft(frontmatter))
      .filter(({ body }) => PLACEHOLDER.test(body))
      .map(({ path }) => path);
    expect(offenders).toEqual([]);
  });

  it('sait refuser — un TODO publié est signalé, le même en draft est toléré', () => {
    const published = frontmatterAndBody('---\ntitle: "x"\nlocale: "en"\n---\nTODO: écrire la vraie accroche.');
    const drafted = frontmatterAndBody('---\ntitle: "x"\nlocale: "en"\ndraft: true\n---\nTODO: en cours.');
    expect(!isDraft(published.frontmatter) && PLACEHOLDER.test(published.body)).toBe(true);
    expect(isDraft(drafted.frontmatter)).toBe(true);
  });
});
