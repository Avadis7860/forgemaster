import { useEffect, useState } from 'react'
import { Alert, Badge, Button, Card, Collapsible, SectionTitle } from '@/components/ui'
import { ApiError } from '@/lib/api'
import {
  useApplyUpdate, useRollbackUpdate, useUpdateRun, useUpdateRuns, useVersion,
} from '@/lib/queries'
import { enVol, liaison } from '@/lib/updateLiaison'
import { RunFollow, RunRow } from './RunFollow'
import { UpdatePreview } from './UpdatePreview'
import { WheelDepot } from './WheelDepot'

/** Le cycle de mise à jour, **depuis le produit** : déposer → prévisualiser → poser → suivre → revenir.
 *
 *  CE QUI DÉCIDE DE TOUTE LA FORME : le daemon qui sert cette page est celui que la MAJ arrête et remplace.
 *  Donc **rien de l'état d'un geste ne vit ici** — pas de `localStorage`, pas de machine à états, pas
 *  d'identifiant de run gardé entre deux vies de la page. Au montage, on REDÉCOUVRE le run en cours par la
 *  liste ; un rechargement pendant la bascule ne perd rien, parce qu'il n'y avait rien à perdre. C'est la
 *  symétrie exacte du serveur, qui relit tout du disque pour la même raison.
 *
 *  Et le silence du daemon n'est pas une erreur : c'est un état ATTENDU, avec un âge. La qualification vit
 *  dans `lib/updateLiaison` (pur, testé à la table), jamais ici. */
export function UpdatePanel() {
  const runs = useUpdateRuns()
  const version = useVersion()
  const poser = useApplyUpdate()
  const revenir = useRollbackUpdate()

  const [choisi, setChoisi] = useState<string | null>(null)
  const [apercu, setApercu] = useState<'apply' | 'rollback' | null>(null)
  const [runChoisi, setRunChoisi] = useState<string | null>(null)
  // L'identifiant du run qu'on vient de lancer. Il ne sert qu'à couvrir le trou entre le 202 et la première
  // lecture RÉUSSIE de ce run-là — trou pendant lequel le daemon est déjà en train de mourir. Dès que la
  // lecture aboutit, c'est l'état du disque qui parle et cet indice ne pèse plus rien.
  const [lance, setLance] = useState<string | null>(null)
  // Indice tenu pour la durée de vie de CETTE page seulement : le SHA servi avant le geste. S'il diffère
  // ensuite, on peut dire la propriété la plus parlante du cycle — le binaire qui répond n'est plus celui
  // qui a reçu le geste. Son absence (page rechargée) ne change rien à la validité du reste.
  const [shaAvant, setShaAvant] = useState<string | null>(null)

  const runId = runChoisi ?? runs.data?.runs[0]?.run ?? null
  const run = useUpdateRun(runId)

  const muette = mort(runs.error) || mort(run.error) || mort(version.error)
  const contact = Math.max(runs.dataUpdatedAt, run.dataUpdatedAt, version.dataUpdatedAt)

  // Une horloge qui ne bat QUE pendant le silence : l'âge affiché doit vieillir sous les yeux, et il n'y a
  // aucune raison de re-rendre la page le reste du temps. Le premier battement arrive à la seconde suivante
  // — pendant cette seconde-là on n'annonce simplement aucun âge, plutôt que d'en inventer un.
  const [maintenant, setMaintenant] = useState(0)
  useEffect(() => {
    if (!muette) return
    const id = setInterval(() => setMaintenant(Date.now()), 1_000)
    return () => clearInterval(id)
  }, [muette])

  const etatConnu = run.data?.state ?? null
  // L'indice de lancement ne vaut que tant qu'on REGARDE le run lancé : sans `runId === lance`, ouvrir un
  // geste ancien dans l'historique rendrait « en vol » un run terminé depuis longtemps — et un silence du
  // daemon passerait alors pour une bascule.
  const attendLeLance = lance !== null && runId === lance && run.data?.run !== lance
  const lien = liaison({
    muette,
    // En vol tant que le disque n'a pas parlé de CE run-là, puis tant que son état n'est pas tranché.
    gesteEnVol: enVol(etatConnu) || attendLeLance,
    depuisMs: contact > 0 && maintenant > contact ? maintenant - contact : null,
    borneMs: runs.data ? runs.data.follow_timeout * 1000 : null,
  })

  function lancer(mode: 'apply' | 'rollback') {
    // L'échec de l'autre verbe n'a plus rien à dire : le laisser afficher son alerte pendant qu'un nouveau
    // geste part montrerait un refus périmé à côté d'un geste vivant.
    poser.reset()
    revenir.reset()
    setShaAvant(version.data?.sha ?? null)
    const suivre = {
      onSuccess: (r: { run: string }) => { setLance(r.run); setRunChoisi(r.run); setApercu(null) },
    }
    if (mode === 'apply' && choisi) poser.mutate(choisi, suivre)
    else if (mode === 'rollback') revenir.mutate(undefined, suivre)
  }

  const sha = version.data?.sha
  const bascule = Boolean(shaAvant && sha && shaAvant !== sha)
  const echecLancement = poser.error ?? revenir.error

  return (
    <Card className="space-y-5 p-5">
      {/* Pas de bouton de rafraîchissement ici : TOUT ce que ce panneau montre bat de lui-même (les runs
          toutes les 5 s, le run suivi toutes les 2 s tant qu'il n'a pas de verdict, la provenance toutes
          les 10 s). Un second bouton identique à celui de la page serait du chrome qui n'ajoute rien. */}
      <SectionTitle eyebrow="instance" title="Mise à jour" />

      <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
        {sha ? (
          <>
            <span>Cette instance sert le build</span>
            <span className="font-mono text-xs text-fg">{sha.slice(0, 7)}</span>
          </>
        ) : (
          // Provenance inconnue : on ne PRÉTEND rien. C'est le cas d'un checkout de développement, où le
          // wheel n'a jamais été tamponné — le dire vaut mieux qu'afficher un tiret.
          <span>Cette instance sert un build non tamponné (installée depuis un checkout, pas un wheel)</span>
        )}
        {version.data?.committed_at && (
          <span className="text-xs text-faint">du {version.data.committed_at.slice(0, 10)}</span>
        )}
        {bascule && (
          <Badge tone="ok" dot>
            le binaire a changé — {shaAvant?.slice(0, 7)} → {sha?.slice(0, 7)}
          </Badge>
        )}
      </div>

      {/* Le transitoire est un `status`, JAMAIS un `<Alert role="alert">` : le marteler à chaque battement
          de sonde hurlerait dans un lecteur d'écran. Les états terminaux, eux, restent des Alert. */}
      {lien.etat !== 'servie' && (
        <div
          role="status"
          aria-live="polite"
          className="space-y-1 rounded-card border border-border bg-surface px-4 py-3"
        >
          <p className="text-sm font-medium text-fg">{lien.titre}</p>
          <p className="text-sm text-muted">{lien.detail}</p>
        </div>
      )}

      {echecLancement && (
        <Alert tone="danger" title="Le geste n'est pas parti">
          <p className="whitespace-pre-wrap">
            {echecLancement instanceof ApiError ? echecLancement.detail : String(echecLancement)}
          </p>
        </Alert>
      )}

      {run.data && <RunFollow run={run.data} />}

      {/* Chaque prévisualisation vit SOUS l'affordance qui l'a demandée. Une seule fente partagée collait le
          refus du retour arrière au bloc de dépôt, et on lisait le refus de travers. */}
      <div className="space-y-4 border-t border-border pt-5">
        <WheelDepot
          choisi={choisi}
          onChoisir={(path) => { setChoisi(path); setApercu('apply') }}
        />
        {apercu === 'apply' && (
          <UpdatePreview
            mode="apply"
            cible={choisi ?? ''}
            onLancer={() => lancer('apply')}
            enCours={poser.isPending}
          />
        )}
      </div>

      <div className="space-y-4 border-t border-border pt-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted">
            Revenir en arrière — binaire <em>et</em> données, ou rien.
          </p>
          <Button variant="secondary" size="sm" onClick={() => setApercu('rollback')}>
            Voir le retour arrière
          </Button>
        </div>
        {apercu === 'rollback' && (
          <UpdatePreview
            mode="rollback"
            cible=""
            onLancer={() => lancer('rollback')}
            enCours={revenir.isPending}
          />
        )}
      </div>

      {runs.data && runs.data.runs.length > 0 && (
        <Collapsible title={`Gestes précédents (${runs.data.total})`} variant="section">
          <ul className="divide-y divide-border">
            {runs.data.runs.map((r) => (
              <RunRow key={r.run} run={r} actif={r.run === runId} onChoisir={() => setRunChoisi(r.run)} />
            ))}
          </ul>
          {runs.data.truncated && (
            <p className="pt-2 text-xs text-faint">
              Liste tronquée : {runs.data.runs.length} affichés sur {runs.data.total}.
            </p>
          )}
        </Collapsible>
      )}
    </Card>
  )
}

/** Un silence RÉSEAU (le `fetch` n'a pas abouti), pas une erreur applicative. Un 409/503 est une réponse —
 *  l'instance a parlé — et ne doit surtout pas être lu comme une bascule en cours. */
function mort(err: unknown): boolean {
  return err instanceof ApiError && err.status === 0
}
