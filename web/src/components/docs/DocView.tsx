import type { ComponentPropsWithoutRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** DocView — rendu Markdown d'une carte docs. `react-markdown` (SÛR : React elements, jamais
 *  `dangerouslySetInnerHTML`) + `remark-gfm` (tables, listes de tâches, autoliens). Chaque élément est mappé
 *  aux tokens `@theme` (source unique, pas de hex en dur). Lazy-chargé par `DocsTab` — react-markdown est
 *  lourd → chunk séparé (le bundle principal reste léger, comme xterm). */
export function DocView({ content }: { content: string }) {
  return (
    <div className="max-w-3xl space-y-3 text-sm leading-relaxed text-fg">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="mt-1 text-2xl font-semibold text-fg">{children}</h1>,
          h2: ({ children }) => (
            <h2 className="mt-6 border-b border-border pb-1 text-xl font-semibold text-fg">{children}</h2>
          ),
          h3: ({ children }) => <h3 className="mt-4 text-base font-semibold text-fg">{children}</h3>,
          p: ({ children }) => <p className="text-muted">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold text-fg">{children}</strong>,
          ul: ({ children }) => <ul className="list-disc space-y-1 pl-6 text-muted">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal space-y-1 pl-6 text-muted">{children}</ol>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer"
              className="text-accent-400 underline decoration-accent-400/40 hover:text-accent-300">
              {children}
            </a>
          ),
          code: ({ className, children }: ComponentPropsWithoutRef<'code'>) =>
            className ? (   // fenced (a une classe language-*) → laissé au <pre> stylé
              <code className={className}>{children}</code>
            ) : (          // inline
              <code className="rounded bg-surface px-1.5 py-0.5 font-mono text-[0.85em] text-fg">{children}</code>
            ),
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded-card border border-border bg-surface p-3 font-mono text-xs text-fg">
              {children}
            </pre>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-accent-500 pl-3 italic text-faint">{children}</blockquote>
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-border px-2 py-1 font-semibold text-fg">{children}</th>
          ),
          td: ({ children }) => <td className="border-b border-border px-2 py-1 text-muted">{children}</td>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
