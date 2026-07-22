// mdx — helpers PURS de préparation de markdown, hors du chunk lazy `DocView` (react-markdown) : `DocReader`
// et `DocView` les partagent sans tirer le renderer lourd.

/** Retire les instructions **MDX** `import …` / `export …` de tête de page (fumadocs & co. déclarent leurs
 *  composants ainsi) : ce ne sont JAMAIS du contenu lisible, mais react-markdown les rendrait comme un
 *  paragraphe de boilerplate en tête — le premier bloc que l'œil rencontre sous le titre. On drope ces lignes
 *  HORS des fences de code (un vrai bloc ```ts import …``` reste intact). PUR. */
export function stripMdxDirectives(md: string): string {
  let inFence = false
  return md
    .split('\n')
    .filter((line) => {
      if (/^\s*(```|~~~)/.test(line)) { inFence = !inFence; return true }
      if (inFence) return true
      return !/^\s*(import|export)\s/.test(line)   // instruction MDX au niveau module → retirée
    })
    .join('\n')
    .replace(/^\n+/, '')                            // pas de vide résiduel avant le vrai 1er bloc
}

/** 1er titre ATX (`# …`) d'un markdown — le titre HUMAIN du document, pour l'en-tête du lecteur (sinon on
 *  répète le slug de fichier). None si absent. PUR. */
export function firstHeading(md: string): string | null {
  for (const line of md.split('\n')) {
    const m = /^#\s+(.+?)\s*$/.exec(line)
    if (m) return m[1]
  }
  return null
}
