import { cn } from '@/lib/cn'
import { Badge, Eyebrow } from '@/components/ui'
import { dotClasses } from '@/lib/statusTone'
import type { JobFrame, TranscriptEvent } from '@/lib/schemas'

type Ev = Exclude<TranscriptEvent, JobFrame>

/** Transcript live d'un worker : log STRUCTURÉ (pas un PTY) des événements normalisés — tours assistant
 *  (texte + outils appelés) et résultats d'outils (ok/erreur). Le contrat vient du daemon (normalize_line). */
export function Transcript({ events }: { events: Ev[] }) {
  return (
    <div className="space-y-3">
      {events.map((ev, i) =>
        ev.type === 'assistant' ? (
          <AssistantTurn key={i} ev={ev} />
        ) : (
          <ToolResults key={i} ev={ev} />
        ),
      )}
    </div>
  )
}

function AssistantTurn({ ev }: { ev: Extract<Ev, { type: 'assistant' }> }) {
  return (
    <div className="rounded-card border border-border bg-surface px-4 py-3">
      <Eyebrow>assistant</Eyebrow>
      {ev.text && <p className="mt-1 whitespace-pre-wrap text-sm text-fg">{ev.text}</p>}
      {ev.tools && ev.tools.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {ev.tools.map((t, j) => (
            <Badge key={j} tone="accent">
              <span className="font-medium">{t.name ?? 'outil'}</span>
              {t.input_summary && <span className="font-mono text-muted">· {t.input_summary}</span>}
            </Badge>
          ))}
        </div>
      )}
      {ev.usage?.output_tokens != null && (
        <p className="mt-2 text-xs text-faint">{ev.usage.output_tokens} tokens</p>
      )}
    </div>
  )
}

function ToolResults({ ev }: { ev: Extract<Ev, { type: 'tool_result' }> }) {
  return (
    <div className="space-y-1 pl-4">
      {ev.results.map((r, j) => (
        <div key={j} className="flex items-start gap-2 text-xs text-muted">
          <span className={cn('mt-1 size-1.5 shrink-0 rounded-pill', dotClasses(r.ok ? 'ok' : 'danger'))} />
          <span className="whitespace-pre-wrap font-mono">
            {r.summary || (r.ok ? '(ok)' : '(erreur)')}
          </span>
        </div>
      ))}
    </div>
  )
}
