import { useEffect, useRef } from 'react'

/** Fait défiler la ligne `line` (1-based) au centre du conteneur scrollable quand elle change — deep-link
 *  depuis la recherche de code ou un permalink épinglé à une ligne. Le conteneur (ref renvoyée, à poser sur le
 *  wrapper `overflow-auto`) doit contenir des lignes portant `data-line="<n>"`. `line` absente → aucun effet.
 *  `dep` (ex. le contenu du fichier) re-déclenche le scroll après un changement de fichier à ligne identique. */
export function useScrollToLine(line: number | undefined, dep?: unknown) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (line == null) return
    // `scrollIntoView?.` : absent sous jsdom (tests) → no-op propre plutôt qu'un throw.
    ref.current?.querySelector(`[data-line="${line}"]`)?.scrollIntoView?.({ block: 'center' })
  }, [line, dep])
  return ref
}
