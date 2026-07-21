import { useState } from 'react'
import { Alert, Button, Card, EmptyState, Input, LoadingState, RefreshButton } from '@/components/ui'
import { DecisionBanner, ReviewEvidence, VerifyEvidence } from '@/components/gate/GateReport'
import { ApiError } from '@/lib/api'
import { useGate, useMerge, useRefixDispatch, useReviewDispatch } from '@/lib/queries'
import type { FeatureWithTasks, RefixResult } from '@/lib/schemas'

// Rendu du compte-rendu d'une passe de correction, par statut (source unique : le backend `dispatch.refix`).
const REFIX_TONE: Record<RefixResult['status'], 'ok' | 'info' | 'warn' | 'danger'> = {
  green: 'ok', still_red: 'info', exhausted: 'warn',
  not_refixable: 'info', not_red: 'info', dispatch_failed: 'danger',
}
const REFIX_TITLE: Record<RefixResult['status'], string> = {
  green: 'Gate re-évalué vert', still_red: 'Toujours rouge', exhausted: 'Borne de correction atteinte',
  not_refixable: 'Non corrigible par un worker', not_red: 'Rien à corriger',
  dispatch_failed: 'Échec de la correction',
}

/** Panneau d'une feature : décision de merge (bannière), évidence Tier-1/Tier-1.5, overrides + GO humain.
 *  Invariant fail-closed : gate vert SANS go ⇒ hold, jamais merge (le backend décide ; le front ne recompose
 *  jamais la décision). Extrait de l'ex-onglet Gate pour être composé dans la surface « Travail ». */
export function GatePanel({ project, feature }: { project: string; feature: FeatureWithTasks }) {
  const gate = useGate(project, feature.slug)
  const merge = useMerge(project, feature.slug)
  const reviewDispatch = useReviewDispatch(project, feature.slug)
  const refix = useRefixDispatch(project, feature.slug)
  const [t1Override, setT1Override] = useState('')
  const [t15Override, setT15Override] = useState('')

  if (gate.isLoading)
    return <Card className="p-5"><LoadingState label="Évaluation du gate…" /></Card>
  if (gate.isError || !gate.data) {
    return (
      <Card className="space-y-3 p-5">
        <Alert tone="danger" title="Gate indisponible">
          {gate.error instanceof ApiError ? gate.error.detail : String(gate.error)}
        </Alert>
        <RefreshButton onClick={() => gate.refetch()} busy={gate.isFetching} />
      </Card>
    )
  }

  const data = gate.data
  const decision = data.decision ?? null

  // Une feature jamais dispatchée n'a pas de branche → rien à merger (le backend renvoie decision=null).
  if (!decision) {
    return (
      <Card className="p-5">
        <EmptyState
          title="Aucune branche à merger"
          description="Cette feature n'a pas encore été dispatchée : aucune branche de travail, donc rien à évaluer ni à merger."
        />
      </Card>
    )
  }

  // Overridable côté humain : un 🔴 reviewer (Tier-1) ou un Tier-1.5 absent/périmé/non-rendu quand l'UI est
  // touchée. JAMAIS un Tier-0 / natif déterministe. On ne montre les champs que si un tel bloqueur existe.
  const t1Overridable = data.review.blocking
  const t15Overridable =
    data.ui_touched && (!data.verify.present || !data.verify.fresh || data.verify.blocking)
  const showOverrides = t1Overridable || t15Overridable

  // GO actif si gate vert (hold → merge) OU si un override explicite est saisi (tentative de levée d'un
  // bloqueur overridable). Le backend tranche : jamais de recomposition de la décision côté front.
  const overridePresent = t1Override.trim() !== '' || t15Override.trim() !== ''
  const canGo = decision.gate_green || overridePresent

  function onGo() {
    merge.mutate({
      go: true,
      t1_override: t1Override.trim() || undefined,
      t15_override: t15Override.trim() || undefined,
    })
  }

  const report = merge.data

  return (
    <Card className="space-y-5 p-5">
      <DecisionBanner decision={decision} headSha={data.head_sha} />

      <div className="grid gap-4 sm:grid-cols-2">
        <ReviewEvidence review={data.review} />
        <VerifyEvidence verify={data.verify} uiTouched={data.ui_touched} />
      </div>

      {(!data.review.present || !data.review.fresh) && (
        <div className="space-y-3 rounded-card border border-border px-4 py-3">
          <p className="text-sm text-fg">
            {!data.review.present
              ? 'Aucune revue Tier-1 sur ce HEAD — le merge reste bloqué tant qu’elle n’est pas produite (elle est normalement produite au dispatch).'
              : 'Revue Tier-1 périmée (le HEAD a bougé depuis) — re-lancer pour ré-ancrer le verdict sur le HEAD courant.'}
          </p>
          <Button
            variant="secondary"
            onClick={() => reviewDispatch.mutate()}
            busy={reviewDispatch.isPending}
            disabled={reviewDispatch.isPending}
          >
            {reviewDispatch.isPending ? 'Review en cours…' : 'Re-lancer la review Tier-1'}
          </Button>
          {reviewDispatch.isError && (
            <Alert tone="danger" title="Échec de la review">
              {reviewDispatch.error instanceof ApiError
                ? reviewDispatch.error.detail
                : String(reviewDispatch.error)}
            </Alert>
          )}
          {reviewDispatch.data && !reviewDispatch.data.reviewed && (
            <Alert tone="warn" title="Review non produite">{reviewDispatch.data.reason}</Alert>
          )}
        </div>
      )}

      {/* Gate rouge par un DÉFAUT DE CODE (refixable) → offre une passe de correction bornée. Complémentaire
          du bloc review ci-dessus (refixable=false quand la review est absente/périmée). Le merge reste GO. */}
      {!decision.gate_green && decision.refixable && (
        <div className="space-y-3 rounded-card border border-border px-4 py-3">
          <p className="text-sm text-fg">
            Gate rouge par un défaut de code. Tu peux dispatcher une passe de correction : un worker reprend la
            branche, corrige les bloqueurs cités, puis le gate est ré-évalué. Le merge reste sous ton GO.
          </p>
          <Button
            variant="secondary"
            onClick={() => refix.mutate()}
            busy={refix.isPending}
            disabled={refix.isPending}
          >
            {refix.isPending ? 'Correction en cours…' : 'Dispatcher une passe de correction'}
          </Button>
          {refix.isError && (
            <Alert tone="danger" title="Échec du dispatch de correction">
              {refix.error instanceof ApiError ? refix.error.detail : String(refix.error)}
            </Alert>
          )}
          {refix.data && (
            <Alert tone={REFIX_TONE[refix.data.status]} title={REFIX_TITLE[refix.data.status]}>
              <p>
                Passe {refix.data.fix_pass}/{refix.data.max_passes}. {refix.data.next_step}
              </p>
              {refix.data.blockers.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {refix.data.blockers.map((b, i) => (
                    <li key={i}>🔴 {b}</li>
                  ))}
                </ul>
              )}
            </Alert>
          )}
        </div>
      )}

      {decision.reasons.length > 0 && (
        <details className="rounded-card border border-border px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-muted">
            Chaîne d'autorité — {decision.reasons.length} point(s)
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-muted">
            {decision.reasons.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-faint">·</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {showOverrides && (
        <div className="space-y-3 rounded-card border border-warn-500/30 bg-warn-500/5 px-4 py-3">
          <p className="text-sm font-medium text-fg">Override humain (raison explicite, tracée)</p>
          {t1Overridable && (
            <label className="block space-y-1">
              <span className="text-xs text-muted">Lever le 🔴 reviewer (Tier-1)</span>
              <Input
                value={t1Override}
                onChange={(e) => setT1Override(e.target.value)}
                placeholder="Pourquoi ce 🔴 est levable à la main…"
              />
            </label>
          )}
          {t15Overridable && (
            <label className="block space-y-1">
              <span className="text-xs text-muted">Lever le Tier-1.5 (preuve e2e)</span>
              <Input
                value={t15Override}
                onChange={(e) => setT15Override(e.target.value)}
                placeholder="Pourquoi le rendu est acceptable sans preuve fraîche…"
              />
            </label>
          )}
          <p className="text-xs text-faint">
            Un override ne lève qu'un 🔴 reviewer ou un Tier-1.5 — jamais un veto Tier-0 / toolchain native.
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <p className="text-sm text-muted">
          {decision.gate_green
            ? 'Le gate est vert. Le merge attend ton GO — le LLM ne merge jamais seul.'
            : 'Le gate est rouge. Corrige les bloqueurs, ou fournis un override explicite pour ceux qui sont levables.'}
        </p>
        <Button variant="primary" onClick={onGo} busy={merge.isPending} disabled={!canGo || merge.isPending}>
          {merge.isPending ? 'Merge en cours…' : 'GO — merger la feature'}
        </Button>
      </div>

      {merge.isError && (
        <Alert tone="danger" title="Échec du merge">
          {merge.error instanceof ApiError ? merge.error.detail : String(merge.error)}
        </Alert>
      )}
      {report?.merged && (
        <Alert tone="ok" title="Feature mergée">
          <p>
            ff dev→main promu{report.merge_sha ? ` (sha ${report.merge_sha.slice(0, 8)})` : ''} ·{' '}
            {report.closed_tasks.length} task(s) fermée(s)
            {report.pending_tasks.length > 0 && ` · ${report.pending_tasks.length} task(s) todo restante(s)`}.
          </p>
        </Alert>
      )}
      {report && !report.merged && (
        <Alert tone="warn" title="Merge non effectué">
          {report.reason}
        </Alert>
      )}
    </Card>
  )
}
