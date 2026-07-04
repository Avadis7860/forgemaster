import type { ComponentPropsWithoutRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** DocView — rendu Markdown d'une carte docs, avec un **rythme typographique de vraie page de doc**.
 *  `react-markdown` (SÛR : React elements, jamais `dangerouslySetInnerHTML`) + `remark-gfm` (tables, code,
 *  autoliens). Chaque bloc est mappé aux tokens `@theme` (source unique) et porte ses propres marges → une
 *  hiérarchie claire et une mesure de lecture confortable (le parent centre la colonne). Lazy-chargé par
 *  `DocsTab` (chunk séparé, comme xterm). */
export function DocView({ content }: { content: string }) {
  return (
    <article className="text-[0.95rem] leading-7 text-muted">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="mb-3 text-3xl font-bold tracking-tight text-fg">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="mt-10 mb-3 border-b border-border pb-2 text-lg font-semibold tracking-tight text-fg">
              {children}
            </h2>
          ),
          h3: ({ children }) => <h3 className="mt-6 mb-1.5 text-base font-semibold text-fg">{children}</h3>,
          p: ({ children }) => <p className="mt-4 first:mt-0">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold text-fg">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          ul: ({ children }) => <ul className="mt-4 list-disc space-y-2 pl-6 marker:text-faint">{children}</ul>,
          ol: ({ children }) => (
            <ol className="mt-4 list-decimal space-y-2 pl-6 marker:text-faint">{children}</ol>
          ),
          li: ({ children }) => <li className="pl-1">{children}</li>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer"
              className="font-medium text-accent-400 underline decoration-accent-400/40 underline-offset-2 hover:text-accent-300">
              {children}
            </a>
          ),
          hr: () => <hr className="my-8 border-border" />,
          blockquote: ({ children }) => (
            <blockquote className="mt-4 border-l-2 border-accent-500 pl-4 italic text-faint">{children}</blockquote>
          ),
          code: ({ className, children }: ComponentPropsWithoutRef<'code'>) =>
            className ? (   // fenced (classe language-*) → laissé au <pre> stylé
              <code className={className}>{children}</code>
            ) : (          // inline
              <code className="rounded border border-border/60 bg-surface px-1.5 py-0.5 font-mono text-[0.82em] text-fg">
                {children}
              </code>
            ),
          pre: ({ children }) => (
            <pre className="mt-4 overflow-x-auto rounded-card border border-border bg-surface p-4 font-mono text-xs leading-6 text-fg">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="mt-4 overflow-x-auto rounded-card border border-border">
              <table className="w-full border-collapse text-left text-sm">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-border bg-surface px-3 py-2 font-semibold text-fg">{children}</th>
          ),
          td: ({ children }) => <td className="border-b border-border/60 px-3 py-2">{children}</td>,
        }}
      >
        {content}
      </ReactMarkdown>
    </article>
  )
}
