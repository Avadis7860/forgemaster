import { useState, type FormEvent } from 'react'
import { Alert, Badge, Button, Input, Segmented } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useWireMcp } from '@/lib/queries'
import type { McpState, McpWireInput } from '@/lib/schemas'

/** Câblage du corpus privé `mcp-catalogs` (instance-level) — partagé entre le **wizard** (`/setup`, étape
 *  « Corpus MCP ») et **Réglages** (carte « Corpus capital »). **Optionnel** : une install publique sans
 *  corpus peut sauter. Déjà câblé → encart d'état (re-câblage via la CLI, hors surface). Deux voies
 *  exclusives (comme la liaison credential) : coller le secret HMAC (≥32c, POSSÉDÉ → ref opaque) ou une
 *  référence BWS (UUID). Le secret ne transite que dans le corps du POST ; la réponse ne porte que la
 *  référence, jamais la valeur. */
export function McpCorpus({ mcp }: { mcp: McpState }) {
  const wire = useWireMcp()
  const [mode, setMode] = useState<'secret' | 'ref'>('secret')
  const [secret, setSecret] = useState('')
  const [ref, setRef] = useState('')
  const [endpoint, setEndpoint] = useState('')

  if (mcp.wired)
    return (
      <div className="space-y-1 text-sm text-muted">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="ok" dot>
            corpus câblé
          </Badge>
          <span className="break-all text-fg">
            {mcp.endpoint ?? 'aucun endpoint — câblage incomplet (`cockpit mcp wire --endpoint <url>`)'}
          </span>
        </div>
        <p className="text-xs text-faint">
          Chaque dispatch worker injecte un <code>.mcp.json</code> valide. Change de secret depuis la CLI
          (<code>cockpit mcp wire</code>).
        </p>
      </div>
    )

  // Aucune cible configurée côté daemon → l'endpoint devient OBLIGATOIRE ici : le serveur refuse (400) un
  // câblage sans cible depuis qu'il n'a plus de défaut en dur. On le dit avant le POST, pas après.
  const endpointRequired = mcp.endpoint === null
  const hasVoie = mode === 'secret' ? secret.trim().length >= 32 : ref.trim().length > 0
  const canSubmit = hasVoie && (!endpointRequired || endpoint.trim().length > 0)

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    const body: McpWireInput = { endpoint: endpoint.trim() || undefined }
    if (mode === 'secret') body.secret = secret.trim()
    else body.ref = ref.trim()
    wire.mutate(body, {
      onSuccess: () => {
        setSecret('')
        setRef('')
      },
    })
  }

  return (
    <form onSubmit={onSubmit} className="space-y-2">
      <p className="text-sm text-muted">
        Câble ton corpus privé <strong>mcp-catalogs</strong> pour que chaque worker interroge la doc à jour et
        les patrons gagnants au dispatch. Une install publique sans corpus peut ignorer cette étape.
      </p>
      <Segmented
        value={mode}
        onChange={setMode}
        ariaLabel="voie de câblage MCP"
        options={[
          { value: 'secret', label: 'Coller le secret' },
          { value: 'ref', label: 'Référence BWS' },
        ]}
      />
      {mode === 'secret' ? (
        <Input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder="secret HMAC partagé (≥ 32 caractères)"
          aria-label="secret HMAC du corpus MCP"
        />
      ) : (
        <Input
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder="référence BWS (UUID)"
          aria-label="référence BWS du secret MCP"
        />
      )}
      <Input
        value={endpoint}
        onChange={(e) => setEndpoint(e.target.value)}
        placeholder={
          endpointRequired
            ? 'endpoint de ton instance (requis) — https://mcp.exemple.org/mcp'
            : `endpoint (actuel ${mcp.endpoint})`
        }
        aria-label="endpoint MCP"
      />
      {endpointRequired && (
        <p className="text-xs text-faint">
          Aucune instance <code>mcp-catalogs</code> n'est configurée sur ce cockpit — il n'y en a pas par
          défaut. Indique l'URL de la tienne.
        </p>
      )}
      {wire.isError && (
        <Alert tone="danger">
          {wire.error instanceof ApiError ? wire.error.detail : 'Échec du câblage.'}
        </Alert>
      )}
      <Button type="submit" variant="primary" busy={wire.isPending} disabled={!canSubmit} className="w-full">
        Câbler le corpus MCP
      </Button>
    </form>
  )
}
