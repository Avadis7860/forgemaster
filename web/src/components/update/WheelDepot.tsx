import { useState, type FormEvent } from 'react'
import { Alert, Badge, Button, FileDrop, LoadingState } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { useStageWheel, useWheels } from '@/lib/queries'
import type { WheelDeposit } from '@/lib/schemas'

/** L'aire de dépôt, côté produit : **HTTP n'a pas de système de fichiers**, cette zone est ce chaînon-là.
 *  Le `path` que le daemon rend est repassé tel quel à la prévisualisation puis à la pose — le front ne
 *  recompose jamais un chemin, il n'en a pas les moyens et ce n'est pas son rôle.
 *
 *  Les deux bornes affichées (`keep`, `max_bytes`) sont **lues de la route**, jamais recopiées ici : un
 *  chiffre recopié dérive en silence le jour où la borne bouge, et l'UI se met alors à mentir sur une règle
 *  qu'elle ne fixe pas.
 *
 *  Les refus du daemon (400 nom · 415 type · 413 taille · 409 collision) sont affichés **tels quels**. Ce
 *  sont des phrases françaises complètes, écrites côté serveur pour être lues par un humain ; les
 *  ré-écrire ici perdrait exactement ce qui y a été mis. */
export function WheelDepot({ choisi, onChoisir }: {
  choisi: string | null
  onChoisir: (path: string) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const aire = useWheels()
  const depot = useStageWheel()

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    depot.mutate(file, {
      onSuccess: (pose) => {
        setFile(null)
        onChoisir(pose.path)          // déposé = choisi : le geste suivant est la prévisualisation
      },
    })
  }

  const bornes = aire.data
    ? `.whl — ${moFr(aire.data.max_bytes)} au plus · les ${aire.data.keep} derniers dépôts sont gardés`
    : '.whl'

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <p className="text-sm font-medium text-fg">Déposer une version</p>
        <p className="text-xs text-muted">
          L'artefact arrive par ici parce qu'il est dans ton navigateur, pas sur le disque de l'instance.
          Rien n'est posé au dépôt : tu verras ce qui se passerait avant de décider.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-3">
        <FileDrop file={file} onFile={setFile} accept=".whl" hint={bornes} />
        <Button type="submit" variant="secondary" size="sm" busy={depot.isPending} disabled={!file}>
          Déposer
        </Button>
        {depot.isError && (
          <Alert tone="danger" title="Dépôt refusé">
            {depot.error instanceof ApiError ? depot.error.detail : String(depot.error)}
          </Alert>
        )}
        {depot.isSuccess && depot.data.pruned.length > 0 && (
          <Alert tone="info">
            La rétention a effacé {depot.data.pruned.length} dépôt(s) plus ancien(s) :{' '}
            <span className="font-mono text-xs">{depot.data.pruned.join(', ')}</span>
          </Alert>
        )}
      </form>

      {aire.isPending ? (
        <LoadingState label="Lecture de l'aire de dépôt…" />
      ) : aire.isError ? (
        <Alert tone="danger" title="Aire de dépôt illisible">
          {aire.error instanceof ApiError ? aire.error.detail : String(aire.error)}
        </Alert>
      ) : aire.data.wheels.length === 0 ? (
        // Une LIGNE, pas un `EmptyState` encadré : la zone de dépôt juste au-dessus porte déjà un cadre
        // pointillé et invite déjà au geste. Deux blocs qui disent la même chose, c'est du chrome.
        <p className="text-xs text-faint">Aucun artefact déposé pour l'instant.</p>
      ) : (
        <ul className="divide-y divide-border">
          {aire.data.wheels.map((w) => (
            <DepotRow key={w.stamp} depot={w} actif={w.path === choisi} onChoisir={() => onChoisir(w.path)} />
          ))}
        </ul>
      )}
    </div>
  )
}

/** Une rangée par dépôt — pas une carte : un item mono-contenu entouré d'un cadre n'ajoute aucune
 *  information. `in_use` est **dit**, parce qu'une rétention qui ne montre pas ses exceptions se lit comme
 *  un cap silencieux ; `sha256: null` (dossier posé à la main) se dit aussi, « je n'ai pas mesuré » n'étant
 *  pas « il n'y a rien ». */
function DepotRow({ depot, actif, onChoisir }: {
  depot: WheelDeposit
  actif: boolean
  onChoisir: () => void
}) {
  return (
    <li>
      {/* Sélection par la VARIANTE, jamais par des classes concurrentes (cf. RunRow). */}
      <Button
        variant={actif ? 'secondary' : 'ghost'}
        size="sm"
        onClick={onChoisir}
        aria-current={actif || undefined}
        className="h-auto w-full justify-start gap-2 py-2"
      >
        <span className="truncate font-mono text-xs">{depot.name}</span>
        <span className="shrink-0 text-xs text-faint">{moFr(depot.size)}</span>
        <span className="shrink-0 font-mono text-xs text-faint">
          {depot.sha256 ? depot.sha256.slice(0, 12) : 'sha non mesuré'}
        </span>
        {depot.in_use && (
          <Badge tone="info" dot>
            nommé par un run
          </Badge>
        )}
      </Button>
    </li>
  )
}

/** Taille lisible. Le canal reçoit un artefact, pas des données : au-delà du Mo il n'y a rien à dire. */
function moFr(octets: number): string {
  if (octets < 1024) return `${octets} o`
  if (octets < 1024 * 1024) return `${Math.round(octets / 1024)} Ko`
  return `${(octets / (1024 * 1024)).toFixed(octets < 10 * 1024 * 1024 ? 1 : 0)} Mo`
}
