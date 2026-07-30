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
