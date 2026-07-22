import { Fragment, type ReactNode } from 'react'
import { common, createLowlight } from 'lowlight'

// lowlight (highlight.js sans DOM) partagé — `common` = ~37 langages courants d'un dépôt. Ce module est
// lazy-chargé (chunk séparé, comme DocView) : lowlight n'entre dans le bundle que quand on ouvre un fichier
// de code coloré, jamais à l'accueil.
const lowlight = createLowlight(common)

// Nœuds hast minimaux (évite une dépendance de types `hast` explicite). lowlight émet des `text` et des
// `element` (span.hljs-*), imbriqués sur ≥1 niveau selon le langage → on aplatit récursivement.
type HNode =
  | { type: 'text'; value: string }
  | { type: 'element'; properties?: { className?: string[] }; children: HNode[] }
  | { type: string }

type Tok = { text: string; cls: string }

/** Aplatit l'arbre hast en jetons plats {texte, classes cumulées}, en propageant les classes des spans
 *  parents (un `hljs-*` imbriqué hérite du contexte). */
function flatten(node: HNode, cls: string, out: Tok[]): void {
  if (node.type === 'text') {
    out.push({ text: (node as { value: string }).value, cls })
    return
  }
  if (node.type === 'element') {
    const el = node as { properties?: { className?: string[] }; children: HNode[] }
    const next = [cls, ...(el.properties?.className ?? [])].filter(Boolean).join(' ')
    for (const child of el.children) flatten(child, next, out)
  }
}

/** Découpe les jetons en LIGNES (sur les `\n` internes aux jetons — un commentaire/chaîne multi-ligne est un
 *  seul span hljs). Préserve la grille n°-de-ligne façon GitHub. */
function toLines(children: HNode[]): Tok[][] {
  const toks: Tok[] = []
  for (const child of children) flatten(child, '', toks)
  const lines: Tok[][] = [[]]
  for (const t of toks) {
    const parts = t.text.split('\n')
    parts.forEach((part, i) => {
      if (i > 0) lines.push([])
      if (part) lines[lines.length - 1].push({ text: part, cls: t.cls })
    })
  }
  return lines
}

/** Visionneuse de fichier de code COLORÉE : même grille n°-de-ligne que le rendu nu, chaque ligne tokenisée
 *  par lowlight (spans `.hljs-*` thémés dans index.css). Langage inconnu / erreur → texte nu (jamais de crash). */
export default function HighlightedCode({ content, lang }: { content: string; lang: string }): ReactNode {
  let lines: Tok[][]
  try {
    lines = toLines(lowlight.highlight(lang, content).children as HNode[])
  } catch {
    lines = content.split('\n').map((l) => (l ? [{ text: l, cls: '' }] : []))
  }
  return (
    <div className="overflow-auto">
      <pre className="min-w-full text-xs leading-relaxed">
        <code className="hljs grid bg-transparent font-mono">
          {lines.map((toks, i) => (
            <span key={i} className="grid grid-cols-[auto_1fr] gap-4 px-4 hover:bg-surface-raised">
              <span className="select-none text-right text-faint">{i + 1}</span>
              <span className="whitespace-pre text-fg">
                {toks.length === 0
                  ? ' '
                  : toks.map((t, j) =>
                      t.cls
                        ? <span key={j} className={t.cls}>{t.text}</span>
                        : <Fragment key={j}>{t.text}</Fragment>,
                    )}
              </span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  )
}
