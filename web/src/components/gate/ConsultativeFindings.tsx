import { useState } from 'react'
import { Badge, LoadingState } from '@/components/ui'
import { useGateVerdicts } from '@/lib/queries'
import { GATE_SEVERITY_TONE, toneFor } from '@/lib/statusTone'
import type { ReviewFinding } from '@/lib/schemas'

// Sévérité consultative (non-bloquante) → clé de ton. Le 🔴 n'apparaît pas ici (il bloque, déjà dans la
// bannière + les blockers) : on ne surface que ce que le gate AVALE aujourd'hui (counts seuls).
const SEV_KEY: Record<string, 'yellow' | 'purple'> = { '🟡': 'yellow', '🟣': 'purple' }

/** Findings 🟡/🟣 CONSULTATIFS d'une review, surfacés PAR FEATURE : le gate n'en montre que les counts, or un
 *  vrai défaut jaune ne doit pas mourir dans la preview éphémère. Repliable (`<details>`) + fetch PARESSEUX (le
 *  verdict complet n'est tiré que déplié). Rendu tissu (rangées `divide-y`, conteneur `warn`), pas un mur de
 *  cartes. `count` = counts.yellow+purple lus du gate ; les corps riches viennent de `GET …/verdicts`. */
export function ConsultativeFindings({
  project,
  feature,
  count,
}: {
  project: string
  feature: string
  count: number
}) {
  const [open, setOpen] = useState(false)
  const q = useGateVerdicts(project, feature, open) // ne fetch que déplié
  if (count <= 0) return null
  const findings = (q.data?.review?.findings ?? []).filter((f) => f.severity in SEV_KEY)
  return (
    <details
      className="rounded-card border border-warn-500/30 bg-warn-500/5 px-4 py-3"
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-sm font-medium text-warn-500">
        Findings consultatifs — {count} 🟡/🟣 (non-bloquant, à statuer)
      </summary>
      <div className="mt-2">
        {q.isLoading ? (
          <LoadingState label="Lecture du verdict…" />
        ) : findings.length === 0 ? (
          <p className="text-sm text-faint">Aucun corps de finding sur le HEAD courant.</p>
        ) : (
          <ul className="divide-y divide-border">
            {findings.map((f, i) => (
              <FindingRow key={i} f={f} />
            ))}
          </ul>
        )}
      </div>
    </details>
  )
}

/** Une rangée finding tissu : sévérité + catégorie (badge), `file:line` (mono), claim, evidence citée. */
function FindingRow({ f }: { f: ReviewFinding }) {
  const sev = SEV_KEY[f.severity]
  const where = f.file ? `${f.file}${f.line != null && f.line !== '' ? `:${f.line}` : ''}` : null
  return (
    <li className="space-y-1 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={toneFor(GATE_SEVERITY_TONE, sev)}>
          {f.severity} {f.category ?? 'finding'}
        </Badge>
        {where && <span className="font-mono text-xs text-faint">{where}</span>}
      </div>
      {f.claim && <p className="text-fg">{f.claim}</p>}
      {f.evidence && <p className="font-mono text-xs text-muted">{f.evidence}</p>}
    </li>
  )
}
