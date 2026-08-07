import { useEffect, useState } from 'react'
import { Alert, Badge, Button, Card, Collapsible, SectionTitle } from '@/components/ui'
import { ApiError } from '@/lib/api'
import {
  useApplyUpdate, useRollbackUpdate, useUpdateAptitude, useUpdateRun, useUpdateRuns, useVersion,
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
  const aptitude = useUpdateAptitude()
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

  // CE QUI DÉSARME, et c'est un AUTRE signal que celui qui explique un silence. Deux différences, chacune
  // pour une panne vécue :
  //  — le run le plus RÉCENT, jamais celui qu'on REGARDE : ouvrir un vieux geste dans l'historique ne doit
  //    pas figer les affordances, et un geste en vol qu'on n'a pas ouvert doit quand même les figer ;
  //  — `running` STRICTEMENT, jamais `enVol` (qui vaut aussi `unknown`) : `unknown` est un AVEU, et un run
  //    mort sans verdict condamnerait le panneau pour toujours. C'est la symétrie exacte du refus serveur
  //    (`_refuse_busy_update` : `running` bloque, `unknown` non).
  // La liste dépense sa seule sonde systemd sur ce run-là — son `running` est donc mesuré, pas supposé.
  const dernier = runs.data?.runs[0] ?? null
  const gesteEnVol = dernier?.state === 'running' || attendLeLance
  const motifEnVol = gesteEnVol
    ? `Un geste de mise à jour est en vol${dernier && dernier.state === 'running'
        ? ` (${dernier.mode === 'rollback' ? 'retour arrière' : 'MAJ'} ${dernier.run})` : ''}` +
      ' — les deux toucheraient le même service et le même lien. L\'instance le refuserait ; ' +
      'ça rouvrira tout seul quand son verdict sera écrit.'
    : null
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

  // L'aptitude ne bat pas (cf. `useUpdateAptitude`) : elle se relit quand le PRODUIT a pu la déplacer,
  // c'est-à-dire quand un geste atteint son verdict. C'est le seul événement du panneau qui change une
  // réponse structurelle — un instantané de plus, un venv de moins, un lien qui a bougé.
  const relireAptitude = aptitude.refetch
  const tranche = run.data && !enVol(run.data.state) ? run.data.run : null
  useEffect(() => { if (tranche) relireAptitude() }, [tranche, relireAptitude])

  const socle = aptitude.data?.deployable
  const retour = aptitude.data?.reversible
  // Trois cas, et le troisième est le piège : `reversible.ok === null` veut dire NON MESURÉ (le socle a
  // refusé, il n'y a pas de « binaire actif » depuis lequel mesurer un retour). On désarme alors comme
  // pour un refus — mais on n'ÉCRIT pas de second motif : celui du socle est déjà en tête, et afficher
  // deux refus pour une seule cause ferait chercher deux réparations.
  const socleRefuse = socle?.ok === false
  const retourRefuse = retour?.ok === false
  const desarme = socleRefuse || retourRefuse

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

      {/* L'aptitude, DITE AU REPOS — sans clic, avant qu'on en ait besoin. Elle vit ici, à côté de la
          provenance, parce qu'elle qualifie l'INSTANCE et non un geste : `_preflight_service` est le socle
          de l'aller ET du retour, donc son refus se dit UNE fois, en tête, et pas une fois par bloc. */}
      {socleRefuse && (
        <Alert tone="warn" title="Cette instance ne sait pas se mettre à jour">
          {/* Le texte intégral du refus, tel que le daemon l'écrit : il NOMME déjà la commande qui répare
              (`forgemaster install-service`, `systemctl daemon-reload`). Le reformuler ici en ferait une
              seconde version à maintenir, qui divergerait au premier remaniement du préflight. */}
          <p className="whitespace-pre-wrap">{socle?.reason}</p>
        </Alert>
      )}

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
            bloque={motifEnVol}
          />
        )}
      </div>

      <div className="space-y-4 border-t border-border pt-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="space-y-1">
            {gesteEnVol ? (
              // Un geste en vol prime sur tout le reste de ce bloc : l'aptitude qu'on afficherait ici
              // (« vers tel instantané ») a été mesurée AVANT le geste et peut déjà être fausse. Dire ce
              // qui bloque MAINTENANT vaut mieux que promettre une cible d'hier.
              <p className="text-sm text-muted">{motifEnVol}</p>
            ) : socleRefuse ? (
              // `reversible.ok` vaut `null` ici : rien n'a été mesuré. On pointe la cause, on ne la
              // ré-écrit pas — un seul refus, une seule réparation à chercher.
              <p className="text-sm text-muted">
                Retour arrière indisponible tant que le socle de déploiement n'est pas en place (voir
                ci-dessus).
              </p>
            ) : retourRefuse ? (
              // Le motif RÉEL remplace la phrase générique. Dire « binaire et données, ou rien » au-dessus
              // d'un bouton mort promettrait une capacité que l'instance n'a pas.
              <p className="whitespace-pre-wrap text-sm text-muted">{retour?.reason}</p>
            ) : (
              <p className="text-sm text-muted">
                Revenir en arrière — binaire <em>et</em> données, ou rien.
              </p>
            )}
            {/* « Dire tôt » vaut dans les deux sens : savoir qu'on PEUT revenir, et VERS QUOI, avant d'en
                avoir besoin. Sans ça la surface ne parlerait que pour refuser. */}
            {!gesteEnVol && retour?.ok && retour.target && (
              <p className="text-xs text-faint">
                Vers <span className="font-mono">{retour.target.snapshot}</span>, par le binaire{' '}
                <span className="font-mono">{retour.target.venv}</span>
              </p>
            )}
          </div>
          {/* Désarmé, pas grisé-mais-cliquable : la doctrine de la 3a·3b (un bouton actif sous un refus
              invite à forcer une porte que le daemon vient de fermer), appliquée AVANT le clic cette fois.
              Depuis le 2026-08-07 elle couvre aussi la CONCURRENCE : c'est cette affordance-là, restée
              armée pendant un retour arrière EN VOL, qui a fait ouvrir le défaut. */}
          {!desarme && !gesteEnVol && (
            <Button variant="secondary" size="sm" onClick={() => setApercu('rollback')}>
              Voir le retour arrière
            </Button>
          )}
        </div>
        {apercu === 'rollback' && (
          <UpdatePreview
            mode="rollback"
            cible=""
            onLancer={() => lancer('rollback')}
            enCours={revenir.isPending}
            bloque={motifEnVol}
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
