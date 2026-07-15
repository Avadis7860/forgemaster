import { Alert, Badge } from '@/components/ui'
import { GATE_SEVERITY_TONE, toneFor } from '@/lib/statusTone'
import type { MergeDecision, ReviewStatus, VerifyStatus } from '@/lib/schemas'

// Pièces présentationnelles PURES de la vue Gate (props uniquement, pas de hook) — testables isolément et
// réutilisables. Le GatePanel (stage « Valider & merger » de la surface Travail) orchestre les données ; ces
// composants rendent la décision et l'évidence.

/** Bannière de décision : l'état de merge (hold / merge autorisé / bloqué) + les bloqueurs si rouge.
 *  Le front ne recompose JAMAIS la décision — il rend `compose_merge_decision` (source unique Python). */
export function DecisionBanner({
  decision,
  headSha,
}: {
  decision: MergeDecision
  headSha?: string | null
}) {
  const tone = decision.gate_green ? 'accent' : 'danger'
  const title = decision.gate_green
    ? decision.human_go
      ? 'Gate vert + GO — merge autorisé'
      : 'Gate vert — en attente du GO humain'
    : 'Gate rouge — merge bloqué'
  return (
    <Alert tone={tone} title={title}>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={decision.gate_green ? 'accent' : 'danger'} dot>
            {decision.decision === 'merge' ? 'merge' : 'hold'}
          </Badge>
          {decision.ui_touched && <Badge tone="info">UI touchée</Badge>}
          {decision.t1_overridden && <Badge tone="warn">override Tier-1</Badge>}
          {decision.t15_overridden && <Badge tone="warn">override Tier-1.5</Badge>}
          {headSha && <span className="font-mono text-xs text-faint">HEAD {headSha.slice(0, 8)}</span>}
        </div>
        {decision.blockers.length > 0 && (
          <ul className="space-y-1 text-sm">
            {decision.blockers.map((b, i) => (
              <li key={i} className="flex gap-2">
                <span>🔴</span>
                <span>{b}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Alert>
  )
}

/** Évidence Tier-1 (review reviewer) : présence, fraîcheur, counts 🔴/🟡/🟣, SHA ancré. */
export function ReviewEvidence({ review }: { review: ReviewStatus }) {
  const c = review.counts
  return (
    <div className="space-y-2 rounded-card border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-fg">Tier-1 — review</p>
        <Badge tone={review.present ? (review.fresh ? 'ok' : 'warn') : 'neutral'} dot>
          {review.present ? (review.fresh ? 'fraîche' : 'périmée') : 'absente'}
        </Badge>
      </div>
      {review.present && c ? (
        <div className="flex flex-wrap gap-1.5">
          <Badge tone={toneFor(GATE_SEVERITY_TONE, 'red')}>🔴 {c.red}</Badge>
          <Badge tone={toneFor(GATE_SEVERITY_TONE, 'yellow')}>🟡 {c.yellow}</Badge>
          <Badge tone={toneFor(GATE_SEVERITY_TONE, 'purple')}>🟣 {c.purple}</Badge>
          {review.blocking && <Badge tone="danger" dot>bloquant</Badge>}
        </div>
      ) : (
        <p className="text-sm text-faint">Aucune revue sur le HEAD — dispatche le reviewer.</p>
      )}
      {review.reviewed_sha && (
        <p className="font-mono text-xs text-faint">revu sur {review.reviewed_sha.slice(0, 8)}</p>
      )}
    </div>
  )
}

/** Évidence Tier-1.5 (feature-verified) : N/A hors UI ; sinon présence, fraîcheur, cibles rendues/échouées. */
export function VerifyEvidence({ verify, uiTouched }: { verify: VerifyStatus; uiTouched: boolean }) {
  return (
    <div className="space-y-2 rounded-card border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-fg">Tier-1.5 — feature-verified</p>
        {uiTouched ? (
          <Badge tone={verify.present ? (verify.fresh ? 'ok' : 'warn') : 'neutral'} dot>
            {verify.present ? (verify.fresh ? 'fraîche' : 'périmée') : 'absente'}
          </Badge>
        ) : (
          <Badge tone="neutral">N/A</Badge>
        )}
      </div>
      {!uiTouched ? (
        <p className="text-sm text-faint">Aucune surface UI touchée — preuve e2e non requise.</p>
      ) : verify.present ? (
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <Badge tone={verify.blocking ? 'danger' : 'ok'} dot>
            {verify.n_failed ?? 0}/{verify.n_targets ?? 0} non rendue(s)
          </Badge>
          {!verify.blocking && verify.ok && <span className="text-faint">rendu prouvé</span>}
        </div>
      ) : (
        <p className="text-sm text-faint">UI touchée mais aucune preuve — prouve le rendu (feature-verified).</p>
      )}
    </div>
  )
}
