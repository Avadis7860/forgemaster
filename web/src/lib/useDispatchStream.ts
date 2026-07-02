// useDispatchStream — pont WebSocket → état React du transcript live d'un job de dispatch.
// Ouvre `WS /ws/dispatch/{job}` (même origine : servi par le daemon en prod, proxifié par Vite en dev),
// valide chaque frame par TranscriptEventSchema (source unique du contrat), accumule les événements
// conversationnels et isole la frame terminale `{type:'job'}`. Le daemon clôt le socket en fin de run.
import { useEffect, useState } from 'react'
import { TranscriptEventSchema, type JobFrame, type TranscriptEvent } from './schemas'
import { wsUrl } from './ws'

export type StreamStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

export interface DispatchStream {
  events: Exclude<TranscriptEvent, JobFrame>[]
  terminal: JobFrame | null
  status: StreamStatus
}

export function useDispatchStream(jobId: string | null): DispatchStream {
  const [events, setEvents] = useState<Exclude<TranscriptEvent, JobFrame>[]>([])
  const [terminal, setTerminal] = useState<JobFrame | null>(null)
  const [status, setStatus] = useState<StreamStatus>('idle')
  const [currentJob, setCurrentJob] = useState<string | null>(jobId)

  // Reset PENDANT le rendu quand le job change (pattern React supporté — pas un effet) : l'état résiduel d'un
  // job précédent n'est jamais exposé, et la souscription WS (effet) redémarre propre. La souscription elle
  // ne pose l'état que dans ses callbacks (onopen/onmessage/onclose), jamais synchrone dans le corps d'effet.
  if (jobId !== currentJob) {
    setCurrentJob(jobId)
    setEvents([])
    setTerminal(null)
    setStatus(jobId ? 'connecting' : 'idle')
  }

  useEffect(() => {
    if (!jobId) return

    const ws = new WebSocket(wsUrl(`/ws/dispatch/${encodeURIComponent(jobId)}`))
    ws.onopen = () => setStatus('open')
    ws.onmessage = (m) => {
      let raw: unknown
      try {
        raw = JSON.parse(m.data as string)
      } catch {
        return // frame illisible → ignorée (jamais de crash sur du bruit réseau)
      }
      const parsed = TranscriptEventSchema.safeParse(raw)
      if (!parsed.success) return
      const ev = parsed.data
      if (ev.type === 'job') {
        setTerminal(ev)
        return
      }
      setEvents((prev) => [...prev, ev]) // ev narrowé hors du type 'job' (const → capté par la closure)
    }
    ws.onerror = () => setStatus('error')
    ws.onclose = () => setStatus((s) => (s === 'error' ? s : 'closed'))

    return () => {
      ws.onclose = null // évite un setState après démontage
      ws.close()
    }
  }, [jobId])

  return { events, terminal, status }
}
