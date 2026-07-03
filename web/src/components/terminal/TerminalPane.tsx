import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { Badge } from '@/components/ui'
import { wsUrl } from '@/lib/ws'
import { buildTheme } from './theme'

export type TermStatus = 'connecting' | 'connected' | 'closed' | 'error'

const STATUS: Record<TermStatus, { tone: 'info' | 'ok' | 'neutral' | 'danger'; label: string }> = {
  connecting: { tone: 'info', label: 'connexion…' },
  connected: { tone: 'ok', label: 'connecté' },
  closed: { tone: 'neutral', label: 'fermé' },
  error: { tone: 'danger', label: 'erreur' },
}

// Thème xterm lu depuis les tokens CSS (@theme) — SOURCE UNIQUE, pas de hex dupliqué en TS (mapping dans
// ./theme). Palette 16 couleurs COMPLÈTE (8 normales + 8 brights). Le renderer DOM d'xterm v5 écrit le
// texte dans le DOM (→ visible pour la boucle visuelle), pas dans un canvas opaque.
function readTheme(el: HTMLElement): Record<string, string> {
  const cs = getComputedStyle(el)
  return buildTheme((name) => cs.getPropertyValue(name))
}

/** Terminal PTY d'un projet : xterm.js + addon-fit ↔ `WS /ws/terminal/{project}`. Frames BINAIRES = frappes
 *  (envoyées telles quelles au PTY) ; frames TEXTE = contrôle `{"type":"resize",cols,rows}`. Le login shell
 *  (`bash -l`) tourne dans la racine du projet côté daemon ; à sa sortie, le socket se ferme. */
export function TerminalPane({ project }: { project: string }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<TermStatus>('connecting')

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const term = new Terminal({
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize: 13,
      cursorBlink: true,
      convertEol: true,
      theme: readTheme(document.documentElement),
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host)
    const safeFit = () => {
      try {
        fit.fit()
      } catch {
        /* conteneur pas encore dimensionné → ignoré (le ResizeObserver rejouera) */
      }
    }
    safeFit()

    const ws = new WebSocket(wsUrl(`/ws/terminal/${encodeURIComponent(project)}`))
    ws.binaryType = 'arraybuffer'
    const enc = new TextEncoder()
    const sendResize = () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      }
    }

    ws.onopen = () => {
      setStatus('connected')
      // Bannière cliente (repère stable pour la boucle visuelle + affordance « où suis-je »), puis on cale la
      // taille du PTY sur celle d'xterm avant que le shell n'émette son prompt.
      term.writeln(`\x1b[2m— session terminal · ${project} (bash -l) —\x1b[0m`)
      safeFit()
      sendResize()
    }
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) term.write(new Uint8Array(ev.data))
      else if (typeof ev.data === 'string') term.write(ev.data)
    }
    ws.onerror = () => setStatus('error')
    ws.onclose = () => setStatus((s) => (s === 'error' ? s : 'closed'))

    // Frappes → PTY en BINAIRE (une frame texte serait interprétée comme un contrôle JSON côté daemon).
    const onData = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(enc.encode(data))
    })
    // Redimensionnement xterm → contrôle TEXTE vers le PTY (TIOCSWINSZ).
    const onResize = term.onResize(sendResize)
    const ro = new ResizeObserver(() => safeFit())
    ro.observe(host)

    return () => {
      ro.disconnect()
      onData.dispose()
      onResize.dispose()
      ws.onclose = null // pas de setState après démontage
      ws.close()
      term.dispose()
    }
  }, [project])

  const st = STATUS[status]
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-faint">
          Login shell (<span className="font-mono">bash -l</span>) — tape{' '}
          <span className="font-mono">claude</span> pour lier ton compte (1ʳᵉ fois : login).
        </p>
        <Badge tone={st.tone} dot>
          {st.label}
        </Badge>
      </div>
      <div
        ref={hostRef}
        className="h-[70vh] min-h-80 overflow-hidden rounded-card border border-border bg-bg p-3"
      />
    </div>
  )
}
