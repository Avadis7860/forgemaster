import { Alert, Badge } from '@/components/ui'
import type { ClaudeAuth } from '@/lib/schemas'

/** Libellé humain de la SOURCE d'auth (jamais la valeur) — d'où la machine tient son droit de spawner. */
function sourceLabel(source: string | null): string {
  switch (source) {
    case 'credentials-file':
      return 'session claude login'
    case 'env-api-key':
      return 'clé ANTHROPIC_API_KEY'
    case 'env-oauth':
      return 'token CLAUDE_CODE_OAUTH_TOKEN'
    default:
      return 'aucune'
  }
}

/** Instruction actionnable UNIQUE (miroir du `AUTH_HINT` backend) — la voie officielle, dans LE terminal. */
export const CLAUDE_LOGIN_HINT =
  'Lance `claude login` dans le terminal de cette machine pour authentifier ton compte. Le cockpit utilise ' +
  "l'auth officielle du CLI `claude` — jamais un token partagé ni embarqué."

/** Indicateur d'auth Claude de l'HÔTE — rend visible ce qui était silencieux : cette machine peut-elle
 *  spawner des workers `claude` ? Deux variantes : `badge` (pastille inline, header/point d'action) et
 *  `block` (encart d'onboarding avec l'instruction `claude login` quand l'auth manque). L'état vient du GET
 *  onboarding (`claude_auth`) — présence, jamais la valeur du token. */
export function ClaudeAuthBadge({ auth }: { auth: ClaudeAuth }) {
  return (
    <Badge tone={auth.authenticated ? 'ok' : 'warn'} dot>
      Compte Claude · {auth.authenticated ? 'connecté' : 'non connecté'}
    </Badge>
  )
}

export function ClaudeAuthBlock({ auth }: { auth: ClaudeAuth }) {
  if (auth.authenticated) {
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <ClaudeAuthBadge auth={auth} />
          <span className="text-xs text-faint">via {sourceLabel(auth.source)}</span>
        </div>
        <p className="text-sm text-muted">
          Cette machine est authentifiée : les workers <code>claude</code> tournent sous <strong>ton</strong>{' '}
          compte. Le cockpit n'embarque, ne partage ni n'injecte aucun credential — chaque hôte s'authentifie
          lui-même.
        </p>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <ClaudeAuthBadge auth={auth} />
      <Alert tone="warn" title="Aucun compte Claude sur cette machine">
        {CLAUDE_LOGIN_HINT} Tant que ce n'est pas fait, le dispatch est <strong>refusé</strong> — jamais d'usage
        silencieux d'un compte hérité.
      </Alert>
    </div>
  )
}
