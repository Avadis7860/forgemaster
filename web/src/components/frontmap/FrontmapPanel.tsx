import { useState } from 'react'
import { Alert, Badge, EmptyState, LoadingState, RefreshButton, Segmented } from '@/components/ui'
import {
  useFrontmapPrimitives,
  useFrontmapRoutes,
  useFrontmapTokens,
} from '@/lib/queries'
import type { FrontmapToken } from '@/lib/schemas'

/** Vue Frontmap : le DESIGN-SYSTEM indexé d'un projet (tokens · primitives · routes), rendu depuis l'index
 *  front-map — parité humaine de ce que le worker interroge via `frontmap` au contrat. Trois vues au
 *  `<Segmented/>` (une à la fois, glanceable) ; rendu tissu (rangées à relief au survol, pas de cartes-dans-
 *  carte). Ouvert au clic dans le modal Ops (regarde-et-ferme), deep-linkable par `?panel=frontmap`. */
type View = 'tokens' | 'primitives' | 'routes'

// Un token couleur (valeur `#…`/`rgb`/`hsl`/`oklch`) mérite une pastille ; sinon on montre la valeur brute.
function isColor(value: string): boolean {
  return /^(#|rgb|hsl|oklch|color\()/i.test(value.trim())
}

function TokenRow({ token }: { token: FrontmapToken }) {
  return (
    <div className="flex items-center gap-3 px-3 py-2 hover:bg-surface-2">
      {isColor(token.value) ? (
        <span
          className="size-4 shrink-0 rounded-pill border border-border"
          style={{ background: token.value }}
          aria-hidden
        />
      ) : (
        <span className="size-4 shrink-0" aria-hidden />
      )}
      <code className="shrink-0 text-xs font-medium text-fg">{token.name}</code>
      <code className="truncate text-xs text-muted">{token.value}</code>
      <span className="ml-auto shrink-0 text-xs tabular-nums text-faint">
        {token.source_file.split('/').pop()}:{token.line}
      </span>
    </div>
  )
}

export function FrontmapPanel({ project }: { project: string }) {
  const [view, setView] = useState<View>('tokens')

  const tokens = useFrontmapTokens(project)
  const primitives = useFrontmapPrimitives(project)
  const routes = useFrontmapRoutes(project)

  const active = view === 'tokens' ? tokens : view === 'primitives' ? primitives : routes

  // Tokens groupés par `group` (accent/status/surface/typography…) → sous-titres, comme le worker les lit.
  const tokenGroups = (() => {
    const by: Record<string, FrontmapToken[]> = {}
    for (const t of tokens.data?.tokens ?? []) (by[t.group] ??= []).push(t)
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b))
  })()

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented<View>
          ariaLabel="Vue du design-system"
          value={view}
          onChange={setView}
          options={[
            { value: 'tokens', label: <>Tokens {tokens.data ? <Badge tone="neutral">{tokens.data.count}</Badge> : null}</> },
            { value: 'primitives', label: <>Primitives {primitives.data ? <Badge tone="neutral">{primitives.data.count}</Badge> : null}</> },
            { value: 'routes', label: <>Routes {routes.data ? <Badge tone="neutral">{routes.data.count}</Badge> : null}</> },
          ]}
        />
        <RefreshButton className="ml-auto" onClick={() => active.refetch()} busy={active.isFetching} />
      </div>

      {active.isLoading ? (
        <LoadingState label="Lecture de l'index front-map…" />
      ) : active.isError ? (
        <Alert tone="danger" title="Design-system indisponible">{active.error.message}</Alert>
      ) : view === 'tokens' ? (
        tokenGroups.length === 0 ? (
          <EmptyState title="Aucun token" description="front-map n'a indexé aucun design token pour ce projet." />
        ) : (
          <div className="space-y-4">
            {tokenGroups.map(([group, rows]) => (
              <div key={group} className="overflow-hidden rounded-card border border-border">
                <div className="border-b border-border bg-surface-2 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
                  {group} <span className="tabular-nums text-faint">· {rows.length}</span>
                </div>
                <div className="divide-y divide-border">
                  {rows.map((t) => <TokenRow key={t.name} token={t} />)}
                </div>
              </div>
            ))}
          </div>
        )
      ) : view === 'primitives' ? (
        (primitives.data?.primitives.length ?? 0) === 0 ? (
          <EmptyState
            title="Aucune primitive"
            description="front-map n'a indexé aucune primitive (composant .tsx) — extraction tree-sitter absente ou front sans DS."
          />
        ) : (
          <div className="overflow-hidden rounded-card border border-border divide-y divide-border">
            {primitives.data?.primitives.map((p) => (
              <div key={p.name} className="flex flex-col gap-0.5 px-3 py-2 hover:bg-surface-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-fg">{p.name}</span>
                  <span className="ml-auto shrink-0 text-xs tabular-nums text-faint">
                    {p.file.split('/').pop()}:{p.line}
                  </span>
                </div>
                {p.lead ? <span className="truncate text-xs text-muted">{p.lead}</span> : null}
              </div>
            ))}
          </div>
        )
      ) : (
        (routes.data?.routes.length ?? 0) === 0 ? (
          <EmptyState title="Aucune route" description="front-map n'a indexé aucune route pour ce projet." />
        ) : (
          <div className="overflow-hidden rounded-card border border-border divide-y divide-border">
            {routes.data?.routes.map((r) => (
              <div key={r.var} className="flex items-center gap-3 px-3 py-2 hover:bg-surface-2">
                <code className="shrink-0 text-xs font-medium text-fg">{r.full_path || '/'}</code>
                {r.component ? <span className="truncate text-xs text-muted">→ {r.component}</span> : null}
                <span className="ml-auto shrink-0 text-xs tabular-nums text-faint">
                  {r.file.split('/').pop()}:{r.line}
                </span>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  )
}
