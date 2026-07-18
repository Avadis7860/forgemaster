import { useEffect, useRef, useState } from 'react'
import { useBlocker } from '@tanstack/react-router'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { Badge, Button, Input } from '@/components/ui'
import { wsUrl } from '@/lib/ws'
import { buildTheme } from './theme'
import { LeaveTerminalConfirm } from './LeaveTerminalConfirm'

export type TermStatus = 'connecting' | 'connected' | 'closed' | 'error'

const STATUS: Record<TermStatus, { tone: 'info' | 'ok' | 'neutral' | 'danger'; label: string }> = {
  connecting: { tone: 'info', label: 'connexion…' },
  connected: { tone: 'ok', label: 'connecté' },
  closed: { tone: 'neutral', label: 'fermé' },
  error: { tone: 'danger', label: 'erreur' },
}

const FONT_MIN = 10
const FONT_MAX = 22
const FONT_KEY = 'cockpit.terminal.fontSize'

function readFontSize(): number {
  try {
    const n = Number(localStorage.getItem(FONT_KEY))
    if (n >= FONT_MIN && n <= FONT_MAX) return n
  } catch {
    /* localStorage indisponible (SSR/test) → défaut */
  }
  return 13
}

function saveFontSize(n: number): void {
  try {
    localStorage.setItem(FONT_KEY, String(n))
  } catch {
    /* best-effort */
  }
}

// Thème xterm lu depuis les tokens CSS (@theme) — SOURCE UNIQUE, pas de hex dupliqué en TS (mapping dans
// ./theme). Palette 16 couleurs COMPLÈTE (8 normales + 8 brights). Le renderer DOM d'xterm v5 écrit le
// texte dans le DOM (→ visible pour la boucle visuelle), pas dans un canvas opaque.
function readTheme(el: HTMLElement): Record<string, string> {
  const cs = getComputedStyle(el)
  return buildTheme((name) => cs.getPropertyValue(name))
}

/** Terminal PTY d'un projet : xterm.js (addons fit + search + web-links) ↔ `WS /ws/terminal/{project}`.
 *  Frames BINAIRES = frappes (envoyées telles quelles au PTY) ; frames TEXTE = contrôle
 *  `{"type":"resize",cols,rows}`. Le login shell (`bash -l`) tourne dans la racine du projet côté daemon ;
 *  à sa sortie le socket se ferme → « Relancer » recrée la session. Barre d'outils : recherche dans le
 *  scrollback, taille de police (persistée), effacer, lancer Claude. */
export function TerminalPane({ project, initialCommand }: { project: string; initialCommand?: string }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const searchRef = useRef<SearchAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  // `initialCommand` (ex. handoff interview) auto-lancé UNE fois par montage — pas à chaque reconnexion
  // (`reconnectKey`) ni sur une reconnexion manuelle. Persiste tant que le projet ne change pas (`key`).
  const sentInitialRef = useRef(false)
  const [status, setStatus] = useState<TermStatus>('connecting')
  const [fontSize, setFontSize] = useState<number>(readFontSize)
  const [reconnectKey, setReconnectKey] = useState(0)
  const [query, setQuery] = useState('')

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    setStatus('connecting')

    const term = new Terminal({
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize,
      cursorBlink: true,
      convertEol: true,
      scrollback: 5000,
      theme: readTheme(document.documentElement),
    })
    const fit = new FitAddon()
    const search = new SearchAddon()
    term.loadAddon(fit)
    term.loadAddon(search)
    term.loadAddon(new WebLinksAddon())
    term.open(host)
    termRef.current = term
    fitRef.current = fit
    searchRef.current = search
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
    wsRef.current = ws
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
      // Handoff auto-run (une fois) : envoie la commande au PTY comme une frappe (frame binaire + newline),
      // exactement comme le bouton « Lancer Claude ». Le shell la lit une fois son prompt prêt.
      if (initialCommand && !sentInitialRef.current) {
        sentInitialRef.current = true
        ws.send(enc.encode(`${initialCommand}\n`))
      }
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
      termRef.current = null
      fitRef.current = null
      searchRef.current = null
      wsRef.current = null
    }
    // fontSize hors deps : appliqué à chaud par l'effet dédié (pas de recréation du terminal à chaque cran).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, reconnectKey])

  // Taille de police appliquée À CHAUD (sans recréer le terminal) + persistée.
  useEffect(() => {
    const term = termRef.current
    if (term) {
      term.options.fontSize = fontSize
      try {
        fitRef.current?.fit()
      } catch {
        /* pas encore dimensionné */
      }
    }
    saveFontSize(fontSize)
  }, [fontSize])

  const connected = status === 'connected'
  // Garde de départ : quitter la vue Ops démonterait ce pane → `ws.close()` → le daemon tue le login shell
  // (SIGHUP) et toute commande en cours (ex. `cockpit interview`). Tant que la session est connectée, on
  // bloque la navigation SPA (confirmation) ET la fermeture d'onglet navigateur (`enableBeforeUnload` natif).
  const leaveBlocker = useBlocker({
    shouldBlockFn: () => connected,
    enableBeforeUnload: () => connected,
    withResolver: true,
  })
  const bumpFont = (delta: number) =>
    setFontSize((n) => Math.min(FONT_MAX, Math.max(FONT_MIN, n + delta)))
  const runSearch = (q: string) => {
    setQuery(q)
    if (q) searchRef.current?.findNext(q)
  }
  const sendToPty = (text: string) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(new TextEncoder().encode(text))
      termRef.current?.focus()
    }
  }

  const st = STATUS[status]
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <p className="mr-auto text-xs text-faint">
          Login shell (<span className="font-mono">bash -l</span>) — tape{' '}
          <span className="font-mono">claude</span> pour lier ton compte (1ʳᵉ fois : login).
        </p>
        <div className="w-full sm:w-52">
          <Input
            value={query}
            onChange={(e) => runSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') searchRef.current?.[e.shiftKey ? 'findPrevious' : 'findNext'](query)
            }}
            placeholder="rechercher…"
            aria-label="Rechercher dans le terminal"
            className="h-8"
          />
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => bumpFont(-1)}
            disabled={fontSize <= FONT_MIN} title="Réduire la police" aria-label="Réduire la police">
            A−
          </Button>
          <span className="w-6 text-center font-mono text-xs text-faint tabular-nums">{fontSize}</span>
          <Button variant="ghost" size="sm" onClick={() => bumpFont(1)}
            disabled={fontSize >= FONT_MAX} title="Agrandir la police" aria-label="Agrandir la police">
            A+
          </Button>
        </div>
        <Button variant="ghost" size="sm" onClick={() => termRef.current?.clear()}
          disabled={!connected} title="Effacer l'écran">
          Effacer
        </Button>
        <Button variant="secondary" size="sm" onClick={() => sendToPty('claude\n')} disabled={!connected}
          title="Lancer Claude Code dans le shell">
          Lancer Claude
        </Button>
        {(status === 'closed' || status === 'error') && (
          <Button variant="primary" size="sm" onClick={() => setReconnectKey((k) => k + 1)}>
            Relancer
          </Button>
        )}
        <Badge tone={st.tone} dot>
          {st.label}
        </Badge>
      </div>
      <div
        ref={hostRef}
        className="min-h-0 flex-1 overflow-hidden rounded-card border border-border bg-bg p-3"
      />
      <LeaveTerminalConfirm blocker={leaveBlocker} />
    </div>
  )
}
