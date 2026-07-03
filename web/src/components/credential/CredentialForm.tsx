import { useState, type FormEvent } from 'react'
import { Alert, Badge, Button, Input } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useLinkCredential, useUnlinkCredential } from '@/lib/queries'

/** Affordance « token par repo » : lie/délie un credential à un projet. **Backend-aware** — voie fichier
 *  (token saisi, masqué → stocké chiffré) vs voie BWS (UUID bring-your-own). Ne montre JAMAIS de valeur de
 *  secret : un token lié n'affiche que sa référence opaque. Partagée entre le panneau Réglages et la vue
 *  projet — une seule affordance, deux emplacements. */
export function CredentialForm({ project, backend, linked, linkedRef, compact = false }: {
  project: string
  backend: string
  linked: boolean
  linkedRef?: string | null
  /** `true` quand l'appelant affiche déjà l'état lié à côté (ligne Réglages) → on masque le badge
   *  redondant, ne laissant que la réf + les actions. */
  compact?: boolean
}) {
  const isBws = backend === 'bws'
  const [open, setOpen] = useState(false)
  const [secret, setSecret] = useState('') // token (voie fichier) OU ref/UUID (voie BWS)
  const [label, setLabel] = useState('')
  const link = useLinkCredential(project)
  const unlink = useUnlinkCredential(project)

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    const value = secret.trim()
    if (!value) return
    const trimmedLabel = label.trim() || undefined
    const body = isBws ? { ref: value, label: trimmedLabel } : { token: value, label: trimmedLabel }
    link.mutate(body, {
      onSuccess: () => {
        setSecret('')
        setLabel('')
        setOpen(false)
      },
    })
  }

  if (linked && !open) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {!compact && (
          <Badge tone="ok" dot>
            token lié
          </Badge>
        )}
        {linkedRef && <code className="font-mono text-xs text-faint">réf {linkedRef.slice(0, 8)}…</code>}
        <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
          Remplacer
        </Button>
        <Button size="sm" variant="danger" busy={unlink.isPending} onClick={() => unlink.mutate()}>
          Délier
        </Button>
      </div>
    )
  }

  if (!open) {
    return (
      <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
        {isBws ? 'Lier un secret BWS (UUID)' : 'Lier un token'}
      </Button>
    )
  }

  return (
    <form onSubmit={onSubmit} className="w-full max-w-sm space-y-2">
      <Input
        type={isBws ? 'text' : 'password'}
        value={secret}
        onChange={(e) => setSecret(e.target.value)}
        placeholder={isBws ? 'UUID du secret Bitwarden' : 'token (ex. GitHub PAT) — stocké chiffré'}
        aria-label={isBws ? 'UUID du secret BWS' : 'token à stocker'}
        autoComplete="off"
      />
      <Input
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        placeholder="libellé (optionnel)"
        aria-label="libellé du credential"
      />
      {link.isError && (
        <Alert tone="danger">
          {link.error instanceof ApiError ? link.error.detail : 'Échec de la liaison.'}
        </Alert>
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" size="sm" busy={link.isPending} disabled={!secret.trim()}>
          Lier
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            setOpen(false)
            setSecret('')
            setLabel('')
          }}
        >
          Annuler
        </Button>
      </div>
      <p className="text-xs text-faint">
        {isBws
          ? 'BWS bring-your-own : crée le secret dans Bitwarden, colle son UUID. Aucune valeur stockée côté cockpit.'
          : 'Le token est chiffré dans le coffre (clé-600) ; la DB ne garde qu’une référence opaque.'}
      </p>
    </form>
  )
}
