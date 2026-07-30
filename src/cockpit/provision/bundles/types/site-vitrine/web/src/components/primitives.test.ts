// primitives.test.ts — garde du VOCABULAIRE DE RELIEF woaw (P2/P6). Leçon calée sur la page-référence : un
// registre `sunken` sans ombre INSÉRÉE lit comme un aplat teinté, pas comme un creux. Cette garde rend ce
// défaut INJOUABLE — le token existe (et est bien un `inset`) ET la primitive `Surface` le câble. Le worker
// peut re-thématiser la VALEUR des ombres (couleur/intensité d'instance), il ne peut pas DÉBRANCHER le creux.
//
// CSS lue par `node:fs` (pas `import.meta.glob('*.css', ?raw)`) : sous `@tailwindcss/vite` ce glob revient
// vide — une garde qui s'appuierait dessus serait vert-à-vide (fausse couverture).
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('../styles/global.css', import.meta.url), 'utf8');
const surface = readFileSync(new URL('./Surface.astro', import.meta.url), 'utf8');

describe('relief woaw — le registre sunken est CREUSÉ, pas juste teinté', () => {
  it('global.css déclare --shadow-sunken comme une ombre insérée', () => {
    const decl = css.match(/--shadow-sunken:\s*([^;]+);/);
    expect(decl, '--shadow-sunken doit être déclaré dans les tokens').not.toBeNull();
    expect(decl![1]).toMatch(/\binset\b/);
  });

  it('Surface.surface-sunken câble box-shadow: var(--shadow-sunken)', () => {
    const rule = surface.match(/\.surface-sunken\s*\{([^}]*)\}/);
    expect(rule, '.surface-sunken doit exister').not.toBeNull();
    expect(rule![1]).toMatch(/box-shadow:\s*var\(--shadow-sunken\)/);
  });

  it('les trois plans de relief (raised/sunken/halo) sont tous déclarés', () => {
    for (const tok of ['--shadow-raised', '--shadow-sunken', '--shadow-halo']) {
      expect(css, `${tok} manquant des tokens de relief`).toContain(tok);
    }
  });
});

describe('relief woaw — le fond sunken est plus SOMBRE que le fond de base', () => {
  // Refinement prouvé sur la page-référence (home) puis gradué au socle : l'ombre insérée seule ne suffit pas si
  // `--color-surface-sunken` est plus CLAIR (ou égal à) `--color-surface` — un creux plus clair que son champ lit
  // à contre-sens. Le token doit être re-thématisable par l'instance, mais la RELATION (sunken plus sombre) est un
  // invariant du langage de relief. Cette garde le verrouille au niveau du token, sans figer une valeur.
  const hex = (token: string): string | null => css.match(new RegExp(`${token}:\\s*(#[0-9a-fA-F]{3,6})`))?.[1] ?? null;

  /** Luminance relative WCAG (0 = noir, 1 = blanc) d'un hex #rgb/#rrggbb. */
  const luminance = (h: string): number => {
    const full = h.length === 4 ? `#${h[1]}${h[1]}${h[2]}${h[2]}${h[3]}${h[3]}` : h;
    const channel = (v: number): number => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    const r = parseInt(full.slice(1, 3), 16);
    const g = parseInt(full.slice(3, 5), 16);
    const b = parseInt(full.slice(5, 7), 16);
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  };

  it('--color-surface-sunken a une luminance strictement inférieure à --color-surface', () => {
    const surface = hex('--color-surface');
    const sunken = hex('--color-surface-sunken');
    expect(surface, '--color-surface doit être un hex').not.toBeNull();
    expect(sunken, '--color-surface-sunken doit être un hex').not.toBeNull();
    expect(luminance(sunken!)).toBeLessThan(luminance(surface!));
  });

  it('sait mesurer — un creux plus clair que son champ serait rejeté', () => {
    expect(luminance('#eef1f6')).toBeLessThan(luminance('#ffffff'));
    expect(luminance('#ffffff')).not.toBeLessThan(luminance('#eef1f6'));
  });
});
